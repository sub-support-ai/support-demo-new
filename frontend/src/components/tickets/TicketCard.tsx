import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Collapse,
  Group,
  Paper,
  Progress,
  Select,
  Stack,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import {
  IconArrowsExchange,
  IconCheck,
  IconMessageCircle,
  IconPlayerPlay,
  IconSparkles,
} from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";

import { getApiError } from "../../api/client";
import {
  formatFriendlyDeadline,
  formatOperatorDeadline,
  getDeadlineColor,
  getDeadlineStatus,
} from "../../lib/sla";
import { useResponseTemplates } from "../../api/responseTemplates";
import type { ResponseTemplate, Ticket, UserRole } from "../../api/types";
import {
  useCreateTicketComment,
  usePromoteTicketToKb,
  useRerouteTicket,
  useResolveTicket,
  useSubmitTicketFeedback,
  useTicketComments,
  useUpdateTicketStatus,
} from "../../api/tickets";
import { AiAssistPanel } from "./AiAssistPanel";
import {
  getDepartmentLabel,
  getStatusLabel,
  getTicketKindColor,
  getTicketKindLabel,
  getTicketPriorityLabel,
} from "../../lib/ticketLabels";

const DEPARTMENT_OPTIONS = [
  { value: "IT", label: "ИТ" },
  { value: "HR", label: "Кадры" },
  { value: "finance", label: "Финансы" },
  { value: "procurement", label: "Закупки" },
  { value: "security", label: "Безопасность" },
  { value: "facilities", label: "Офис и помещения" },
  { value: "documents", label: "Документооборот" },
];

function getCorrectionLagSeconds(createdAt: string): number {
  const createdTime = new Date(createdAt).getTime();
  if (Number.isNaN(createdTime)) {
    return 0;
  }
  return Math.max(0, Math.round((Date.now() - createdTime) / 1000));
}

function formatDateTime(value?: string | null): string | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function renderTemplate(template: ResponseTemplate, ticket: Ticket) {
  const values: Record<string, string> = {
    requester_name: ticket.requester_name || "коллега",
    requester_email: ticket.requester_email || "",
    office: ticket.office || "офис не указан",
    affected_item: ticket.affected_item || "объект не указан",
    request_type: ticket.request_type || "запрос",
    request_details: ticket.request_details || "детали не указаны",
    department: ticket.department,
    title: ticket.title,
  };

  return template.body.replace(/\{([a-z_]+)\}/g, (_match, key: string) => {
    return values[key] ?? "";
  });
}

/** Прогресс-бар SLA: «осталось Xч из Yч» + дружелюбный срок. Тикает раз в минуту. */
function SlaProgress({ ticket }: { ticket: Ticket }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  if (!ticket.sla_deadline_at) {
    return null;
  }
  const opts = { breached: ticket.is_sla_breached, now };
  const status = getDeadlineStatus(ticket.sla_deadline_at, opts);
  if (!status) {
    return null;
  }
  const color = getDeadlineColor(status);
  const friendly = formatFriendlyDeadline(ticket.sla_deadline_at, opts);
  const relative = formatOperatorDeadline(ticket.sla_deadline_at, opts);

  const startMs = ticket.sla_started_at ? new Date(ticket.sla_started_at).getTime() : NaN;
  const endMs = new Date(ticket.sla_deadline_at).getTime();
  let pct: number;
  if (!Number.isNaN(startMs) && endMs > startMs) {
    pct = Math.min(100, Math.max(0, ((now.getTime() - startMs) / (endMs - startMs)) * 100));
  } else {
    pct = status === "breached" ? 100 : 0;
  }

  return (
    <div className="sla-progress">
      <Group justify="space-between" gap="xs" wrap="nowrap">
        <Text size="xs" fw={600} c={color}>
          {status === "breached" ? "Срок ответа истёк" : `Ответим ${friendly}`}
        </Text>
        {relative && (
          <Text size="xs" c="dimmed">
            {relative}
          </Text>
        )}
      </Group>
      <Progress value={pct} size="sm" radius="xl" color={color} mt={4} />
    </div>
  );
}

export function TicketCard({
  ticket,
  currentUserRole,
  role,
  selectable,
  selected,
  onSelect,
}: {
  ticket: Ticket;
  currentUserRole?: UserRole;
  role?: UserRole;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (id: number) => void;
}) {
  const viewerRole = currentUserRole ?? role ?? "user";
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [aiPanelOpen, setAiPanelOpen] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [internalComment, setInternalComment] = useState(true);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [acceptedAiResponse, setAcceptedAiResponse] = useState(true);
  const [routingWasCorrect, setRoutingWasCorrect] = useState(true);
  const [rerouteOpen, setRerouteOpen] = useState(false);
  const [rerouteDepartment, setRerouteDepartment] = useState<string | null>(null);
  const [rerouteReason, setRerouteReason] = useState("");

  const comments = useTicketComments(ticket.id, commentsOpen);
  const createComment = useCreateTicketComment();
  const updateStatus = useUpdateTicketStatus();
  const rerouteTicket = useRerouteTicket();
  const resolveTicket = useResolveTicket();
  const feedback = useSubmitTicketFeedback();

  const isOperator = viewerRole === "agent" || viewerRole === "admin";
  const isOwner = viewerRole === "user";
  const isClosed = ticket.status === "closed" || ticket.status === "resolved";
  const promoteToKb = usePromoteTicketToKb();
  const [promotedKbId, setPromotedKbId] = useState<number | null>(null);
  const canOperate =
    isOperator &&
    ticket.status !== "pending_user" &&
    ticket.confirmed_by_user &&
    !isClosed;
  const canComment = isOperator && ticket.confirmed_by_user;
  const mutationError =
    updateStatus.error ??
    rerouteTicket.error ??
    resolveTicket.error ??
    createComment.error ??
    comments.error;
  const createdAt = formatDateTime(ticket.created_at);

  const templates = useResponseTemplates({
    department: ticket.department,
    requestType: ticket.request_type,
    enabled: commentsOpen && canOperate,
  });

  const templateOptions = useMemo(
    () =>
      templates.data?.map((template) => ({
        value: String(template.id),
        label: template.request_type
          ? `${template.title} · ${template.request_type}`
          : template.title,
      })) ?? [],
    [templates.data],
  );

  function handleInsertDraft(text: string) {
    setCommentText(text);
    setInternalComment(false);
    setCommentsOpen(true);
    setAiPanelOpen(false);
  }

  async function handleCreateComment() {
    const content = commentText.trim();
    if (!content) {
      return;
    }
    await createComment.mutateAsync({
      ticketId: ticket.id,
      payload: { content, internal: internalComment },
    });
    setCommentText("");
    setSelectedTemplateId(null);
  }

  function handleTemplateSelect(templateId: string | null) {
    setSelectedTemplateId(templateId);
    const template = templates.data?.find((item) => String(item.id) === templateId);
    if (!template) {
      return;
    }
    setCommentText(renderTemplate(template, ticket));
    setInternalComment(false);
  }

  async function handleReroute() {
    const department = rerouteDepartment;
    const reason = rerouteReason.trim();
    if (!department || !reason || department === ticket.department) {
      return;
    }
    await rerouteTicket.mutateAsync({
      ticketId: ticket.id,
      payload: {
        department: department as "IT" | "HR" | "finance" | "procurement" | "security" | "facilities" | "documents",
        reason,
      },
    });
    setRerouteOpen(false);
    setRerouteDepartment(null);
    setRerouteReason("");
  }

  return (
    <Paper className="ticket-card" withBorder>
      <Stack gap={6}>
        <Group justify="space-between" align="start" wrap="nowrap">
            <Group gap={8} align="flex-start" style={{ flex: 1, minWidth: 0 }}>
            {selectable && (
              <Checkbox
                checked={selected ?? false}
                mt={4}
                style={{ flexShrink: 0 }}
                onChange={() => onSelect?.(ticket.id)}
                aria-label={`Выбрать тикет #${ticket.id}`}
              />
            )}
            <div style={{ flex: 1, minWidth: 0 }}>
              <Title order={4}>{ticket.title}</Title>
              {createdAt && (
                <Text size="xs" c="dimmed">
                  Создан {createdAt}
                </Text>
              )}
            </div>
          </Group>
          <Badge style={{ flexShrink: 0 }}>{getStatusLabel(ticket.status)}</Badge>
        </Group>

        <Text size="sm" lineClamp={3}>
          {ticket.body}
        </Text>

        {(ticket.requester_name || ticket.office || ticket.affected_item) && (
          <Text size="xs" c="dimmed">
            {[ticket.requester_name, ticket.office, ticket.affected_item]
              .filter(Boolean)
              .join(" · ")}
          </Text>
        )}

        {(ticket.request_type || ticket.request_details) && (
          <div className="ticket-form-summary">
            {ticket.request_type && (
              <Text size="xs" fw={600}>
                {ticket.request_type}
              </Text>
            )}
            {ticket.request_details && (
              <Text size="xs" c="dimmed" lineClamp={2}>
                {ticket.request_details}
              </Text>
            )}
          </div>
        )}

        <Group gap="xs">
          {isOperator && (
            <Badge variant="filled" color={getTicketKindColor(ticket.ticket_kind)} size="xs">
              {getTicketKindLabel(ticket.ticket_kind)}
            </Badge>
          )}
          <Badge variant="light">{getDepartmentLabel(ticket.department)}</Badge>
          <Badge variant="light">{getTicketPriorityLabel(ticket)}</Badge>
          {(ticket.reopen_count ?? 0) > 0 && (
            <Badge color="orange" variant="light">
              Повторно открыт: {ticket.reopen_count}
            </Badge>
          )}
          {isOperator && ticket.sla_escalated_at && (
            <Badge color="orange" variant="light">
              SLA эскалирован {formatDateTime(ticket.sla_escalated_at)}
            </Badge>
          )}
        </Group>

        {ticket.confirmed_by_user && !isClosed && <SlaProgress ticket={ticket} />}

        {mutationError && (
          <Alert color="red" variant="light">
            {getApiError(mutationError)}
          </Alert>
        )}

        {canOperate && (
          <Stack gap={6}>
            <Group justify="flex-end">
              <Button
                size="xs"
                variant={aiPanelOpen ? "light" : "subtle"}
                color="violet"
                leftSection={<IconSparkles size={14} />}
                onClick={() => setAiPanelOpen((v) => !v)}
              >
                AI-помощник
              </Button>
            </Group>

            <Collapse in={aiPanelOpen}>
              <Paper withBorder p={8} radius="sm" bg="var(--mantine-color-violet-light)">
                <AiAssistPanel ticketId={ticket.id} onInsertDraft={handleInsertDraft} />
              </Paper>
            </Collapse>

            <Group gap={8} justify="flex-end">
              <Checkbox
                size="xs"
                label="Ответ AI подошёл"
                checked={acceptedAiResponse}
                onChange={(event) =>
                  setAcceptedAiResponse(event.currentTarget.checked)
                }
              />
              <Checkbox
                size="xs"
                label="Роутинг верный"
                checked={routingWasCorrect}
                onChange={(event) =>
                  setRoutingWasCorrect(event.currentTarget.checked)
                }
              />
            </Group>
            <Group gap="xs" justify="flex-end">
              {ticket.status !== "in_progress" && (
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<IconPlayerPlay size={14} />}
                  loading={updateStatus.isPending}
                  onClick={() =>
                    updateStatus.mutate({
                      ticketId: ticket.id,
                      payload: { status: "in_progress" },
                    })
                  }
                >
                  В работу
                </Button>
              )}
              <Button
                size="xs"
                variant="light"
                color="blue"
                leftSection={<IconArrowsExchange size={14} />}
                disabled={updateStatus.isPending || resolveTicket.isPending}
                onClick={() => setRerouteOpen((value) => !value)}
              >
                Передать
              </Button>
              <Button
                size="xs"
                color="green"
                leftSection={<IconCheck size={14} />}
                loading={resolveTicket.isPending}
                onClick={() =>
                  resolveTicket.mutate({
                    ticketId: ticket.id,
                    payload: {
                      agent_accepted_ai_response: acceptedAiResponse,
                      routing_was_correct: routingWasCorrect,
                      correction_lag_seconds: getCorrectionLagSeconds(
                        ticket.created_at,
                      ),
                    },
                  })
                }
              >
                Закрыть
              </Button>
            </Group>
            <Collapse in={rerouteOpen}>
              <Stack className="ticket-reroute-panel" gap={6}>
                <Group grow align="start">
                  <Select
                    label="Новый отдел"
                    data={DEPARTMENT_OPTIONS.filter(
                      (option) => option.value !== ticket.department,
                    )}
                    value={rerouteDepartment}
                    placeholder="Выберите отдел"
                    onChange={setRerouteDepartment}
                  />
                  <Textarea
                    label="Причина передачи"
                    value={rerouteReason}
                    minRows={2}
                    maxRows={4}
                    autosize
                    maxLength={500}
                    placeholder="Например: вопрос относится к доступам ИБ, а не к ИТ"
                    onChange={(event) => setRerouteReason(event.currentTarget.value)}
                  />
                </Group>
                <Group justify="flex-end" gap="xs">
                  <Button
                    size="xs"
                    variant="subtle"
                    color="gray"
                    disabled={rerouteTicket.isPending}
                    onClick={() => setRerouteOpen(false)}
                  >
                    Отмена
                  </Button>
                  <Button
                    size="xs"
                    loading={rerouteTicket.isPending}
                    disabled={
                      !rerouteDepartment ||
                      rerouteDepartment === ticket.department ||
                      !rerouteReason.trim()
                    }
                    onClick={handleReroute}
                  >
                    Передать запрос
                  </Button>
                </Group>
              </Stack>
            </Collapse>
          </Stack>
        )}

        {isOperator && isClosed && (
          <Group gap="xs" align="center">
            <Button
              size="xs"
              variant="light"
              color="violet"
              loading={promoteToKb.isPending}
              disabled={promotedKbId !== null}
              onClick={async () => {
                try {
                  const result = await promoteToKb.mutateAsync(ticket.id);
                  setPromotedKbId(result.article_id);
                } catch {
                  // ошибка покажется через mutationError ниже / снаружи
                }
              }}
            >
              Сделать статью KB
            </Button>
            {promotedKbId !== null && (
              <Text size="xs" c="dimmed">
                Черновик статьи создан — на ревью у админа
              </Text>
            )}
            {promoteToKb.error && (
              <Text size="xs" c="red">
                Не удалось подготовить статью автоматически. Проверьте тикет и попробуйте позже.
              </Text>
            )}
          </Group>
        )}

        {isOwner && isClosed && (
          <Group gap="xs">
            <Button
              size="xs"
              color="teal"
              loading={feedback.isPending}
              onClick={() =>
                feedback.mutate({
                  ticketId: ticket.id,
                  payload: { feedback: "helped" },
                })
              }
            >
              Помогло
            </Button>
            <Button
              size="xs"
              variant="light"
              color="orange"
              loading={feedback.isPending}
              onClick={() =>
                feedback.mutate({
                  ticketId: ticket.id,
                  payload: { feedback: "not_helped", reopen: true },
                })
              }
            >
              Не помогло, открыть снова
            </Button>
          </Group>
        )}

        {canComment && (
          <Stack gap={6}>
            <Group justify="flex-end">
              <Button
                size="xs"
                variant="subtle"
                leftSection={<IconMessageCircle size={14} />}
                onClick={() => setCommentsOpen((value) => !value)}
              >
                Комментарии
              </Button>
            </Group>

            {commentsOpen && (
              <Stack className="ticket-comments" gap={6}>
                {comments.data?.length ? (
                  comments.data.map((comment) => (
                    <div className="ticket-comment" key={comment.id}>
                      <Group justify="space-between" gap="xs">
                        <Text size="xs" fw={600}>
                          {comment.author_username}
                        </Text>
                        <Group gap={6}>
                          <Badge size="xs" variant="light">
                            {comment.internal ? "Внутренний" : "Для пользователя"}
                          </Badge>
                          <Text size="xs" c="dimmed">
                            {formatDateTime(comment.created_at)}
                          </Text>
                        </Group>
                      </Group>
                      <Text size="sm">{comment.content}</Text>
                    </div>
                  ))
                ) : (
                  <Text size="sm" c="dimmed">
                    Комментариев пока нет.
                  </Text>
                )}

                {canOperate && (
                  <Select
                    placeholder="Вставить шаблон ответа"
                    data={templateOptions}
                    value={selectedTemplateId}
                    clearable
                    searchable
                    nothingFoundMessage="Шаблонов нет"
                    disabled={templates.isLoading || !templateOptions.length}
                    onChange={handleTemplateSelect}
                  />
                )}

                <Textarea
                  value={commentText}
                  minRows={2}
                  maxRows={5}
                  autosize
                  maxLength={4000}
                  placeholder="Кратко зафиксируйте ход работы или решение"
                  onChange={(event) => setCommentText(event.currentTarget.value)}
                />
                <Group justify="space-between">
                  <Checkbox
                    checked={internalComment}
                    label="Внутренний комментарий"
                    onChange={(event) =>
                      setInternalComment(event.currentTarget.checked)
                    }
                  />
                  <Button
                    size="xs"
                    loading={createComment.isPending}
                    disabled={!commentText.trim()}
                    onClick={handleCreateComment}
                  >
                    Добавить
                  </Button>
                </Group>
              </Stack>
            )}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}
