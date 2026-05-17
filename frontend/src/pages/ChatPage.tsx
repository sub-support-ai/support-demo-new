import {
  Alert,
  Badge,
  Button,
  Group,
  LoadingOverlay,
  Paper,
  ScrollArea,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import {
  IconClipboardList,
  IconMessageCircle,
  IconPlus,
} from "@tabler/icons-react";
import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  useConversations,
  useCreateConversation,
  useEscalateConversation,
  useMessages,
  useSendMessage,
} from "../api/conversations";
import { getApiError } from "../api/client";
import { useMe } from "../api/auth";
import {
  useConfirmTicket,
  useDeclineTicket,
  useTickets,
  useUpdateTicketDraft,
} from "../api/tickets";
import type {
  Conversation,
  EscalationContext,
  IntakeState,
  Ticket,
  TicketDraftUpdate,
} from "../api/types";
import { Composer } from "../components/chat/Composer";
import { EscalationCard } from "../components/chat/EscalationCard";
import { MessageBubble } from "../components/chat/MessageBubble";
import { PrefilledTicketPanel } from "../components/tickets/PrefilledTicketPanel";
import { TicketWizard } from "../components/tickets/TicketWizard";
import { findPotentialDuplicates } from "../lib/duplicates";
import { getDepartmentLabel, getStatusLabel } from "../lib/ticketLabels";
import { validateEmail } from "../lib/validation";
import { useAuth } from "../stores/auth";

function formatConversationDate(value?: string | null) {
  if (!value) {
    return "Старый диалог";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Старый диалог";
  }

  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function getConversationDate(conversation: Conversation, tickets?: Ticket[]) {
  const ticket = tickets?.find((item) => item.conversation_id === conversation.id);
  return formatConversationDate(
    conversation.created_at ??
      conversation.updated_at ??
      ticket?.created_at ??
      ticket?.updated_at,
  );
}

function getConversationTitle(conversation: Conversation, tickets?: Ticket[]) {
  const ticket = tickets?.find((item) => item.conversation_id === conversation.id);
  if (ticket?.title) {
    return ticket.title;
  }

  if (conversation.status === "active") {
    return "Диалог без запроса";
  }

  return getStatusLabel(conversation.status);
}

const INTAKE_FIELD_LABELS: Record<string, string> = {
  requester_name: "Заявитель",
  requester_email: "Email",
  office: "Офис",
  affected_item: "Что затронуто",
  problem: "Проблема",
  symptoms: "Симптомы",
  business_impact: "Влияние на работу",
  what_tried: "Что пробовали",
  urgency_reason: "Почему срочно",
  affected_users_count: "Сколько пользователей",
  is_business_stopped: "Работа остановлена",
  is_security_or_safety_risk: "Риск безопасности",
  incident_type: "Тип инцидента",
  system_or_account: "Система или учётная запись",
  what_happened: "Что произошло",
  what_user_did: "Что уже сделали",
  time_detected: "Когда обнаружили",
};

const DEFAULT_SIDE_PANEL_WIDTH = 480;
const MIN_SIDE_PANEL_WIDTH = 360;
const MAX_SIDE_PANEL_WIDTH = 900;

function clampSidePanelWidth(width: number) {
  return Math.min(MAX_SIDE_PANEL_WIDTH, Math.max(MIN_SIDE_PANEL_WIDTH, width));
}

/** Предупреждение для поля intake — только для заполненных значений. */
function getFieldWarning(field: string, value: string): string | null {
  if (!value.trim()) return null;
  if (field === "requester_email") return validateEmail(value) ?? null;
  return null;
}

function IntakeStatePanel({ state }: { state: IntakeState }) {
  const fields = state.fields ?? {};
  const requiredFields = state.required_fields ?? [];
  const missingFields = state.missing_fields ?? [];
  const visibleFields = requiredFields.length ? requiredFields : Object.keys(fields);

  return (
    <Paper withBorder p="md" className="quiet-panel">
      <Stack gap="sm">
        <Group justify="space-between" align="start">
          <div>
            <Title order={4}>Данные для запроса</Title>
            <Text size="sm" c="dimmed">
              Система собирает контекст перед созданием черновика.
            </Text>
          </div>
          <Badge color={missingFields.length ? "yellow" : "teal"} variant="light">
            Не хватает: {missingFields.length}
          </Badge>
        </Group>

        <Group gap="xs">
          {state.department && (
            <Badge variant="light">Отдел: {getDepartmentLabel(state.department)}</Badge>
          )}
          {state.request_type && <Badge variant="light">Тип: {state.request_type}</Badge>}
        </Group>

        <Stack gap={6}>
          {visibleFields.map((field) => {
            const rawValue = fields[field];
            const value = typeof rawValue === "string" ? rawValue : "";
            const filled = value.trim().length > 0;
            const warning = filled ? getFieldWarning(field, value) : null;
            const icon = !filled ? "❌" : warning ? "⚠️" : "✅";
            const color = !filled ? "dimmed" : warning ? "orange" : "teal";
            return (
              <Group key={field} justify="space-between" gap="xs" wrap="nowrap">
                <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
                  <Text size="sm" c={color}>
                    {icon} {INTAKE_FIELD_LABELS[field] ?? field}
                  </Text>
                  {warning && (
                    <Text size="xs" c="orange" pl={22}>
                      {warning}
                    </Text>
                  )}
                </Stack>
                <Text size="sm" ta="right" lineClamp={2} c={filled ? undefined : "dimmed"} style={{ minWidth: 0, maxWidth: "55%" }}>
                  {filled ? value : "не заполнено"}
                </Text>
              </Group>
            );
          })}
        </Stack>

        {state.last_question && missingFields.length > 0 && (
          <Alert color="yellow" variant="light">
            {state.last_question}
          </Alert>
        )}
      </Stack>
    </Paper>
  );
}

export function ChatPage() {
  const { token } = useAuth();
  const me = useMe(Boolean(token));
  const conversations = useConversations();
  const createConversation = useCreateConversation();
  const sendMessage = useSendMessage();
  const escalate = useEscalateConversation();
  const confirmTicket = useConfirmTicket();
  const declineTicket = useDeclineTicket();
  const updateTicketDraft = useUpdateTicketDraft();
  const tickets = useTickets();
  const [activeConversationId, setActiveConversationId] = useState<number>();
  // Черновики хранятся per-conversation, чтобы переключение чатов не стирало данные
  const [draftTickets, setDraftTickets] = useState<Record<number, Ticket>>({});
  const [awaitingAiConversationId, setAwaitingAiConversationId] =
    useState<number>();
  // Отслеживаем, для какого диалога идёт отправка — чтобы loading не «протекал» в другие чаты
  const [sendingConvId, setSendingConvId] = useState<number | undefined>();
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const pageGridRef = useRef<HTMLDivElement | null>(null);
  const [composerText, setComposerText] = useState("");
  const [dialogHistoryExpanded, setDialogHistoryExpanded] = useState(false);
  const [sidePanelWidth, setSidePanelWidth] = useState(DEFAULT_SIDE_PANEL_WIDTH);
  const [wizardActiveFor, setWizardActiveFor] = useState<Set<number>>(new Set());

  const activeConversation = useMemo(() => {
    return conversations.data?.find((item) => item.id === activeConversationId);
  }, [activeConversationId, conversations.data]);

  // Диалоги отсортированы: новые сверху
  const sortedConversations = useMemo(
    () =>
      [...(conversations.data ?? [])].sort(
        (a, b) =>
          new Date(b.created_at ?? 0).getTime() -
          new Date(a.created_at ?? 0).getTime(),
      ),
    [conversations.data],
  );

  // Черновик для текущего активного диалога
  const draftTicket =
    activeConversationId != null ? (draftTickets[activeConversationId] ?? null) : null;

  const restoredTicket = useMemo(() => {
    if (!activeConversationId) {
      return null;
    }

    return (
      tickets.data
        ?.filter((ticket) => ticket.conversation_id === activeConversationId)
        .sort(
          (left, right) =>
            new Date(right.created_at).getTime() -
            new Date(left.created_at).getTime(),
        )[0] ?? null
    );
  }, [activeConversationId, tickets.data]);

  // draftTickets[activeConversationId] всегда привязан к нужному диалогу
  const activeTicket = draftTicket ?? restoredTicket;
  const isDraftMode = Boolean(activeTicket);

  // Потенциальные дубликаты — открытые тикеты пользователя с тем же
  // affected_item или (department + request_type). Считаем здесь, чтобы
  // не загружать тикеты повторно внутри PrefilledTicketPanel и не плодить
  // зависимости от React Query в презентационном компоненте.
  const potentialDuplicates = useMemo(() => {
    if (!activeTicket || !tickets.data) return [];
    return findPotentialDuplicates(activeTicket, tickets.data);
  }, [activeTicket, tickets.data]);
  const isAiProcessing = activeConversation?.status === "ai_processing";
  const isAwaitingAiResponse =
    awaitingAiConversationId !== undefined &&
    awaitingAiConversationId === activeConversationId;
  const shouldPollMessages = isAiProcessing || isAwaitingAiResponse;

  // Метки для каждой стадии обработки (пользователь не знает, что это «псевдо»).
  const AI_STAGE_LABELS: Record<string, string> = {
    thinking: "Анализирую вопрос...",
    searching: "Ищу в базе знаний...",
    found_kb: "Нашёл подходящую статью...",
    generating: "Формирую ответ...",
  };
  const aiStageLabel = shouldPollMessages
    ? (activeConversation?.ai_stage
        ? (AI_STAGE_LABELS[activeConversation.ai_stage] ?? "Обрабатываю запрос...")
        : "Обрабатываю запрос...")
    : "";
  // Блокируем ввод только пока AI обрабатывает текущий ответ. Даже после
  // отправки запроса чат остаётся живым: пользователь может вернуться позже
  // и продолжить решать проблему или добавить контекст.
  const composerDisabled = shouldPollMessages;
  useEffect(() => {
    if (!activeConversationId && sortedConversations.length) {
      setActiveConversationId(sortedConversations[0].id);
    }
  }, [activeConversationId, sortedConversations]);

  const messages = useMessages(activeConversationId, shouldPollMessages);
  const latestEscalationMessageId = useMemo(() => {
    const escalationMessages =
      messages.data?.filter(
        (message) => message.role === "ai" && message.requires_escalation,
      ) ?? [];
    return escalationMessages[escalationMessages.length - 1]?.id;
  }, [messages.data]);
  const hasPendingEscalationPrompt = Boolean(
    latestEscalationMessageId && activeConversation && !activeTicket,
  );
  const isRequestPanelMode = isDraftMode || hasPendingEscalationPrompt;
  const dialogHistoryCollapsed = isRequestPanelMode && !dialogHistoryExpanded;

  useEffect(() => {
    setDialogHistoryExpanded(false);
  }, [activeTicket?.id, hasPendingEscalationPrompt]);

  // Фиксируем, что isAiProcessing был true в течение текущего ожидания.
  // Нужно для определения перехода true→false без ложного срабатывания
  // в короткое окно между setAwaitingAiConversationId и первым рефетчем
  // conversations (когда isAiProcessing ещё false из кеша).
  const sawAiProcessingRef = useRef(false);
  useEffect(() => {
    if (!isAwaitingAiResponse) {
      sawAiProcessingRef.current = false;
      return;
    }
    if (isAiProcessing) {
      sawAiProcessingRef.current = true;
    }
  }, [isAwaitingAiResponse, isAiProcessing]);

  useEffect(() => {
    if (!isAwaitingAiResponse) {
      return;
    }

    let latestUserMessageId = 0;
    let latestAiMessageId = 0;
    for (const message of messages.data ?? []) {
      if (message.role === "user") {
        latestUserMessageId = Math.max(latestUserMessageId, message.id);
      }
      if (message.role === "ai") {
        latestAiMessageId = Math.max(latestAiMessageId, message.id);
      }
    }

    if (latestUserMessageId > 0 && latestAiMessageId > latestUserMessageId) {
      setAwaitingAiConversationId(undefined);
    } else if (sawAiProcessingRef.current && !isAiProcessing && latestUserMessageId > 0) {
      // Backend завершил обработку (переход ai_processing→active),
      // но AI-сообщение не появилось — джоба упала окончательно.
      setAwaitingAiConversationId(undefined);
    }
  }, [isAwaitingAiResponse, isAiProcessing, messages.data]);

  // Страховочный таймаут: если через 3 минуты ответа нет — снимаем блок.
  // Покрывает случай когда воркер не запущен и статус никогда не меняется.
  useEffect(() => {
    if (!isAwaitingAiResponse) return;
    const timer = setTimeout(() => setAwaitingAiConversationId(undefined), 180_000);
    return () => clearTimeout(timer);
  }, [isAwaitingAiResponse]);

  useEffect(() => {
    if (activeTicket && awaitingAiConversationId === activeConversationId) {
      setAwaitingAiConversationId(undefined);
    }
  }, [activeConversationId, activeTicket, awaitingAiConversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.data?.length, shouldPollMessages]);

  async function ensureConversation() {
    const activeConversationExists =
      activeConversationId &&
      (
        !conversations.data ||
        conversations.data.some((item) => item.id === activeConversationId)
      );
    if (activeConversationExists) {
      return activeConversationId;
    }
    const conversation = await createConversation.mutateAsync();
    setActiveConversationId(conversation.id);
    return conversation.id;
  }

  async function handleSend(content: string) {
    try {
      const conversationId = await ensureConversation();
      setSendingConvId(conversationId);
      const response = await sendMessage.mutateAsync({ conversationId, content });
      if (response.ai_job_id !== null && response.ai_job_id !== undefined) {
        setAwaitingAiConversationId(conversationId);
      } else {
        setAwaitingAiConversationId(undefined);
      }
    } catch {
      // Ошибка уже хранится в mutation/query state и показывается в Alert.
    } finally {
      setSendingConvId(undefined);
    }
  }

  async function handleNewConversation() {
    try {
      const conversation = await createConversation.mutateAsync();
      setActiveConversationId(conversation.id);
    } catch {
      // Ошибка уже хранится в mutation state и показывается в Alert.
    }
  }

  async function handleStartTicketWizard() {
    try {
      const conversationId = activeTicket
        ? (await createConversation.mutateAsync()).id
        : await ensureConversation();
      setActiveConversationId(conversationId);
      setWizardActiveFor((prev) => {
        const next = new Set(prev);
        next.add(conversationId);
        return next;
      });
    } catch {
      // Ошибка уже хранится в mutation state и показывается в Alert.
    }
  }

  function handleWizardCancel(conversationId: number) {
    setWizardActiveFor((prev) => {
      const next = new Set(prev);
      next.delete(conversationId);
      return next;
    });
  }

  function handleWizardTicketCreated(conversationId: number, ticket: Ticket) {
    setDraftTickets((prev) => ({ ...prev, [conversationId]: ticket }));
    setActiveConversationId(conversationId);
    setAwaitingAiConversationId(undefined);
    setSendingConvId(undefined);
    setWizardActiveFor((prev) => {
      const next = new Set(prev);
      next.delete(conversationId);
      return next;
    });
  }

  async function handleEscalate(conversationId: number, context: EscalationContext) {
    try {
      const response = await escalate.mutateAsync({
        conversationId,
        context,
      });
      setDraftTickets((prev) => ({ ...prev, [conversationId]: response.ticket }));
      setActiveConversationId(conversationId);
      setAwaitingAiConversationId(undefined);
      setSendingConvId(undefined);
    } catch {
      // Ошибка уже хранится в mutation state и показывается в Alert.
    }
  }

  async function handleConfirm() {
    if (!activeTicket || activeConversationId == null) {
      return;
    }
    try {
      const ticket = await confirmTicket.mutateAsync(activeTicket.id);
      setDraftTickets((prev) => ({ ...prev, [activeConversationId]: ticket }));
    } catch {
      // Ошибка уже хранится в mutation state и показывается в Alert.
    }
  }

  async function handleDecline() {
    if (!activeTicket || activeConversationId == null) {
      return;
    }
    try {
      const ticket = await declineTicket.mutateAsync(activeTicket.id);
      setDraftTickets((prev) => ({ ...prev, [activeConversationId]: ticket }));
      await conversations.refetch();
    } catch {
      // Ошибка уже хранится в mutation state и показывается в Alert.
    }
  }

  async function handleSaveDraft(payload: TicketDraftUpdate) {
    if (!activeTicket || activeConversationId == null) {
      return;
    }
    try {
      const ticket = await updateTicketDraft.mutateAsync({
        ticketId: activeTicket.id,
        payload,
      });
      setDraftTickets((prev) => ({ ...prev, [activeConversationId]: ticket }));
    } catch {
      // Ошибка уже хранится в mutation state и показывается в Alert.
    }
  }

  function handleSidePanelResizePointerDown(
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);

    function handlePointerMove(moveEvent: PointerEvent) {
      const gridRect = pageGridRef.current?.getBoundingClientRect();
      const rightEdge = gridRect?.right ?? window.innerWidth;
      setSidePanelWidth(clampSidePanelWidth(rightEdge - moveEvent.clientX));
    }

    function handlePointerUp() {
      document.removeEventListener("pointermove", handlePointerMove);
      document.removeEventListener("pointerup", handlePointerUp);
    }

    document.addEventListener("pointermove", handlePointerMove);
    document.addEventListener("pointerup", handlePointerUp, { once: true });
  }

  function handleSidePanelResizeKeyDown(
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    setSidePanelWidth((width) =>
      clampSidePanelWidth(width + (event.key === "ArrowLeft" ? 32 : -32)),
    );
  }

  const error =
    conversations.error ||
    messages.error ||
    sendMessage.error ||
    escalate.error ||
    confirmTicket.error ||
    declineTicket.error ||
    updateTicketDraft.error ||
    createConversation.error ||
    me.error ||
    tickets.error;
  const requestContext = me.data?.request_context ?? null;
  const showAiConfidence =
    me.data?.role === "agent" || me.data?.role === "admin";
  const wizardActive =
    activeConversation != null &&
    !activeTicket &&
    wizardActiveFor.has(activeConversation.id);
  const pageGridStyle = {
    "--chat-side-width": `${sidePanelWidth}px`,
  } as CSSProperties;

  return (
    <div
      ref={pageGridRef}
      className={`page-grid${isRequestPanelMode ? " draft-mode" : ""}`}
      style={pageGridStyle}
    >
      <Paper className="chat-panel" withBorder>
        <Group justify="space-between" mb="md">
          <div>
            <Title order={2}>Чат поддержки</Title>
            <Text size="sm" c="dimmed">
              {activeConversation
                ? getStatusLabel(activeConversation.status)
                : "Новый диалог"}
            </Text>
          </div>
          <Group gap="xs">
            <Button
              variant="subtle"
              leftSection={<IconClipboardList size={16} />}
              loading={createConversation.isPending}
              disabled={Boolean(activeTicket?.confirmed_by_user)}
              onClick={handleStartTicketWizard}
            >
              Оформить запрос
            </Button>
            <Button
              variant="light"
              leftSection={<IconPlus size={16} />}
              loading={createConversation.isPending}
              onClick={handleNewConversation}
            >
              Новый
            </Button>
          </Group>
        </Group>

        {error && (
          <Alert color="red" variant="light" mb="md">
            {getApiError(error)}
          </Alert>
        )}

        <div className="chat-surface">
          {wizardActive && activeConversation ? (
            <div className="wizard-host">
              <TicketWizard
                conversation={activeConversation}
                isAiProcessing={shouldPollMessages}
                me={me.data}
                onCancel={() => handleWizardCancel(activeConversation.id)}
                onTicketCreated={(ticket) =>
                  handleWizardTicketCreated(activeConversation.id, ticket)
                }
              />
            </div>
          ) : (
            <>
              <LoadingOverlay visible={messages.isFetching && !messages.data} />
              <ScrollArea className="messages-scroll" type="auto">
                <Stack gap="sm" p="md">
                  {!messages.data?.length && (
                    <div className="empty-state">
                      <IconMessageCircle size={34} />
                      <Text fw={600}>Нет сообщений</Text>
                    </div>
                  )}
                  {messages.data?.map((message) => (
                    <MessageBubble
                      key={message.id}
                      message={message}
                      showAiConfidence={showAiConfidence}
                    />
                  ))}
                  {shouldPollMessages && (
                    <div className="message-row ai">
                      <Paper className="message-bubble ai thinking-bubble" withBorder>
                        <Group gap="xs" mb={4} align="center">
                          <Text size="xs" fw={600} c="dimmed">
                            AI
                          </Text>
                        </Group>
                        <Group gap={8} align="center">
                          <span className="thinking-dots" aria-hidden>
                            <span /> <span /> <span />
                          </span>
                          <Text size="sm" c="dimmed">
                            {aiStageLabel}
                          </Text>
                        </Group>
                      </Paper>
                    </div>
                  )}
                  <div ref={bottomRef} />
                </Stack>
              </ScrollArea>
              {activeConversation?.intake_state?.last_question && !shouldPollMessages && (
                <Alert color="blue" variant="light" p="xs" mx="md" mb="xs">
                  <Text size="sm">{activeConversation.intake_state.last_question}</Text>
                </Alert>
              )}
              <Composer
                loading={
                  (sendMessage.isPending && sendingConvId === activeConversationId) ||
                  createConversation.isPending
                }
                disabled={composerDisabled}
                value={composerText}
                onChange={setComposerText}
                onSend={handleSend}
              />
            </>
          )}
        </div>
      </Paper>

      <div
        className="chat-side-resizer"
        role="separator"
        aria-label="Изменить ширину черновика и диалогов"
        aria-orientation="vertical"
        tabIndex={0}
        onPointerDown={handleSidePanelResizePointerDown}
        onKeyDown={handleSidePanelResizeKeyDown}
      />

      <div className="side-panel">
        <Paper
          withBorder
          p="md"
          className={`quiet-panel conversations-panel${
            dialogHistoryCollapsed ? " collapsed" : ""
          }`}
        >
          <Group justify="space-between" mb="sm">
            <Title order={4}>Диалоги</Title>
            <Group gap="xs">
              <Badge variant="light">{conversations.data?.length ?? 0}</Badge>
              {isRequestPanelMode && (
                <Button
                  size="xs"
                  variant="subtle"
                  onClick={() => setDialogHistoryExpanded((value) => !value)}
                >
                  {dialogHistoryCollapsed ? "Развернуть" : "Свернуть"}
                </Button>
              )}
            </Group>
          </Group>
          {dialogHistoryCollapsed ? (
            <div className="conversation-summary">
              <Text size="sm" fw={600} lineClamp={1}>
                {activeConversation
                  ? getConversationTitle(activeConversation, tickets.data)
                  : "Активный диалог"}
              </Text>
              <Group justify="space-between" gap="xs" wrap="nowrap">
                <Badge size="sm" variant="light">
                  {activeConversation
                    ? getStatusLabel(activeConversation.status)
                    : "Новый"}
                </Badge>
                {activeConversation && (
                  <Text size="xs" c="dimmed">
                    {getConversationDate(activeConversation, tickets.data)}
                  </Text>
                )}
              </Group>
            </div>
          ) : (
            <Stack gap="xs" className="conversation-list">
              {sortedConversations.length ? (
                sortedConversations.map((conversation) => (
                  <button
                    key={conversation.id}
                    type="button"
                    className={`conversation-item${
                      conversation.id === activeConversationId ? " active" : ""
                    }`}
                    onClick={() => {
                      setActiveConversationId(conversation.id);
                    }}
                  >
                    <Text className="conversation-item-title" lineClamp={2}>
                      {getConversationTitle(conversation, tickets.data)}
                    </Text>
                    <Group justify="space-between" gap="xs" wrap="nowrap">
                      <Badge size="sm" variant="light">
                        {getStatusLabel(conversation.status)}
                      </Badge>
                      <Text size="xs" c="dimmed">
                        {getConversationDate(conversation, tickets.data)}
                      </Text>
                    </Group>
                  </button>
                ))
              ) : (
                <Text size="sm" c="dimmed">
                  Диалогов пока нет.
                </Text>
              )}
            </Stack>
          )}
        </Paper>

        {activeTicket ? (
          <PrefilledTicketPanel
            ticket={activeTicket}
            intakeState={activeConversation?.intake_state}
            me={me.data}
            potentialDuplicates={potentialDuplicates}
            confirmLoading={confirmTicket.isPending}
            declineLoading={declineTicket.isPending}
            saveLoading={updateTicketDraft.isPending}
            onConfirm={handleConfirm}
            onDecline={handleDecline}
            onSave={handleSaveDraft}
          />
        ) : hasPendingEscalationPrompt && activeConversation ? (
          <Paper withBorder p="md" className="quiet-panel draft-escalation-panel">
            <Stack gap="sm">
              <div>
                <Title order={4}>Черновик запроса</Title>
                <Text size="sm" c="dimmed">
                  Заполните данные здесь. Описание из чата попадёт в черновик.
                </Text>
              </div>
              <EscalationCard
                contextDefaults={requestContext}
                intakeState={activeConversation.intake_state}
                disabled={composerDisabled}
                loading={escalate.isPending}
                onEscalate={(context) =>
                  handleEscalate(activeConversation.id, context)
                }
              />
            </Stack>
          </Paper>
        ) : (
          <Paper withBorder p="md" className="quiet-panel">
            <Title order={4}>Черновик запроса</Title>
            <Text size="sm" c="dimmed">
              Появится после эскалации диалога.
            </Text>
          </Paper>
        )}
      </div>
    </div>
  );
}
