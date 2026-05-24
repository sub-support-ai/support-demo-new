import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  LoadingOverlay,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Tabs,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconDownload } from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useMe } from "../api/auth";
import { getApiError } from "../api/client";
import { useBulkUpdateTickets, useTickets } from "../api/tickets";
import type { TicketBulkAction, TicketBulkResponse } from "../api/types";
import { BulkActionBar } from "../components/tickets/BulkActionBar";
import { BulkResultModal } from "../components/tickets/BulkResultModal";
import { TicketCard } from "../components/tickets/TicketCard";
import { downloadTicketsCsv } from "../lib/csv";
import { getStatusLabel } from "../lib/ticketLabels";
import { useAuth } from "../stores/auth";

const STATUS_OPTIONS = [
  "pending_user",
  "confirmed",
  "in_progress",
  "closed",
  "resolved",
].map((status) => ({ value: status, label: getStatusLabel(status) }));

const DEPARTMENT_OPTIONS = [
  { value: "IT", label: "ИТ" },
  { value: "HR", label: "Кадры" },
  { value: "finance", label: "Финансы" },
  { value: "procurement", label: "Закупки" },
  { value: "security", label: "Безопасность" },
  { value: "facilities", label: "Офис и помещения" },
  { value: "documents", label: "Документооборот" },
];

const SLA_OPTIONS = [
  { value: "overdue", label: "SLA просрочен" },
  { value: "active", label: "SLA в норме" },
];

type TicketQueue =
  | "active"
  | "new"
  | "in_progress"
  | "overdue"
  | "unassigned"
  | "pending_user"
  | "resolved"
  | "all";

const OPERATOR_QUEUES: Array<{
  value: TicketQueue;
  label: string;
  description: string;
}> = [
  {
    value: "active",
    label: "Активные",
    description: "Все подтверждённые запросы, которые ещё не закрыты.",
  },
  {
    value: "new",
    label: "Новые",
    description: "Запросы отправлены в отдел, агент ещё не начал работу.",
  },
  {
    value: "in_progress",
    label: "В работе",
    description: "Запросы уже взяты в обработку.",
  },
  {
    value: "overdue",
    label: "Просрочены",
    description: "Запросы с нарушенным SLA требуют первоочередной реакции.",
  },
  {
    value: "unassigned",
    label: "Без исполнителя",
    description: "Подтверждённые запросы, которым не назначен агент.",
  },
  {
    value: "pending_user",
    label: "Ждут пользователя",
    description: "Черновики, которые пользователь ещё не отправил в отдел.",
  },
  {
    value: "resolved",
    label: "Завершены",
    description: "Решённые и закрытые запросы.",
  },
  {
    value: "all",
    label: "Все",
    description: "Полный список запросов с учётом фильтров.",
  },
];

const USER_QUEUES: Array<{
  value: TicketQueue;
  label: string;
  description: string;
}> = [
  {
    value: "active",
    label: "Активные",
    description: "Ваши отправленные запросы, которые ещё обрабатываются.",
  },
  {
    value: "pending_user",
    label: "Ожидают подтверждения",
    description: "AI собрал черновик — проверьте и нажмите «Отправить».",
  },
  {
    value: "resolved",
    label: "Завершены",
    description: "Решённые и закрытые обращения.",
  },
  {
    value: "all",
    label: "Все",
    description: "Все ваши обращения с учётом фильтров.",
  },
];

function isActiveTicket(ticket: { status: string; confirmed_by_user: boolean }) {
  return ticket.confirmed_by_user && ["confirmed", "in_progress"].includes(ticket.status);
}

function isResolvedTicket(ticket: { status: string }) {
  return ["resolved", "closed", "declined"].includes(ticket.status);
}

function matchesQueue(
  ticket: {
    status: string;
    confirmed_by_user: boolean;
    is_sla_breached?: boolean;
    agent_id?: number | null;
  },
  queue: TicketQueue,
) {
  if (queue === "all") return true;
  if (queue === "active") return isActiveTicket(ticket);
  if (queue === "new") return ticket.status === "confirmed";
  if (queue === "in_progress") return ticket.status === "in_progress";
  if (queue === "overdue") return isActiveTicket(ticket) && Boolean(ticket.is_sla_breached);
  if (queue === "unassigned") {
    return isActiveTicket(ticket) && ticket.agent_id == null;
  }
  if (queue === "pending_user") {
    return ticket.status === "pending_user" && !ticket.confirmed_by_user;
  }
  if (queue === "resolved") return isResolvedTicket(ticket);
  return true;
}

function getPriorityRank(priority?: string | null, userPriority?: number | null) {
  const normalized = priority?.toLowerCase();
  if (normalized === "критический") return 0;
  if (normalized === "высокий") return 1;
  if (normalized === "средний") return 2;
  if (normalized === "низкий") return 3;
  if (typeof userPriority === "number") {
    return Math.max(0, Math.min(4, userPriority - 1));
  }
  return 4;
}

function getTicketSortScore(ticket: {
  status: string;
  is_sla_breached?: boolean;
  agent_id?: number | null;
  ai_priority?: string | null;
  user_priority?: number | null;
  created_at: string;
}) {
  const slaScore = ticket.is_sla_breached ? 0 : 1;
  const unassignedScore =
    ticket.agent_id == null && ["confirmed", "in_progress"].includes(ticket.status)
      ? 0
      : 1;
  const statusScore = ticket.status === "confirmed" ? 0 : ticket.status === "in_progress" ? 1 : 2;
  const priorityScore = getPriorityRank(ticket.ai_priority, ticket.user_priority);
  const createdTime = new Date(ticket.created_at).getTime();
  return [
    slaScore,
    unassignedScore,
    statusScore,
    priorityScore,
    Number.isNaN(createdTime) ? 0 : -createdTime,
  ];
}

function compareTicketsByQueue(left: Parameters<typeof getTicketSortScore>[0], right: Parameters<typeof getTicketSortScore>[0]) {
  const leftScore = getTicketSortScore(left);
  const rightScore = getTicketSortScore(right);
  for (let index = 0; index < leftScore.length; index += 1) {
    if (leftScore[index] !== rightScore[index]) {
      return leftScore[index] - rightScore[index];
    }
  }
  return 0;
}

type SortMode = "smart" | "newest" | "oldest" | "priority" | "sla";

const SORT_OPTIONS: { value: SortMode; label: string }[] = [
  { value: "smart", label: "Умная (по умолчанию)" },
  { value: "newest", label: "Сначала новые" },
  { value: "oldest", label: "Сначала старые" },
  { value: "priority", label: "По приоритету" },
  { value: "sla", label: "По сроку SLA" },
];

type SortableTicket = Parameters<typeof getTicketSortScore>[0] & {
  sla_deadline_at?: string | null;
};

type TicketGroupId =
  | "overdue"
  | "unassigned"
  | "new"
  | "in_progress"
  | "pending_user"
  | "resolved"
  | "other";

const TICKET_GROUPS: Array<{
  id: TicketGroupId;
  label: string;
  description: string;
  color: string;
}> = [
  {
    id: "overdue",
    label: "Сначала SLA",
    description: "Просроченные запросы требуют реакции в первую очередь.",
    color: "red",
  },
  {
    id: "unassigned",
    label: "Без исполнителя",
    description: "Нужно назначить ответственного или взять в работу.",
    color: "orange",
  },
  {
    id: "new",
    label: "Новые",
    description: "Запросы подтверждены пользователем и ждут начала обработки.",
    color: "blue",
  },
  {
    id: "in_progress",
    label: "В работе",
    description: "Исполнитель уже начал обработку.",
    color: "teal",
  },
  {
    id: "pending_user",
    label: "Ждут пользователя",
    description: "Черновики ещё не отправлены в отдел.",
    color: "yellow",
  },
  {
    id: "resolved",
    label: "Завершены",
    description: "Решённые и закрытые запросы.",
    color: "gray",
  },
  {
    id: "other",
    label: "Остальные",
    description: "Запросы без отдельного операционного статуса.",
    color: "gray",
  },
];

function getTicketGroupId(ticket: {
  status: string;
  confirmed_by_user: boolean;
  is_sla_breached?: boolean;
  agent_id?: number | null;
}): TicketGroupId {
  if (isActiveTicket(ticket) && ticket.is_sla_breached) return "overdue";
  if (isActiveTicket(ticket) && ticket.agent_id == null) return "unassigned";
  if (ticket.status === "confirmed") return "new";
  if (ticket.status === "in_progress") return "in_progress";
  if (ticket.status === "pending_user" && !ticket.confirmed_by_user) return "pending_user";
  if (isResolvedTicket(ticket)) return "resolved";
  return "other";
}

function pickTicketComparator(
  mode: SortMode,
): (left: SortableTicket, right: SortableTicket) => number {
  const createdMs = (t: SortableTicket) => {
    const ms = new Date(t.created_at).getTime();
    return Number.isNaN(ms) ? 0 : ms;
  };
  // Срок SLA: сначала те, у кого дедлайн ближе/просрочен; без дедлайна — в конец.
  const slaMs = (t: SortableTicket) => {
    if (!t.sla_deadline_at) return Number.POSITIVE_INFINITY;
    const ms = new Date(t.sla_deadline_at).getTime();
    return Number.isNaN(ms) ? Number.POSITIVE_INFINITY : ms;
  };
  switch (mode) {
    case "newest":
      return (a, b) => createdMs(b) - createdMs(a);
    case "oldest":
      return (a, b) => createdMs(a) - createdMs(b);
    case "priority":
      return (a, b) =>
        getPriorityRank(a.ai_priority, a.user_priority) -
          getPriorityRank(b.ai_priority, b.user_priority) || createdMs(b) - createdMs(a);
    case "sla":
      return (a, b) => slaMs(a) - slaMs(b) || createdMs(b) - createdMs(a);
    default:
      return compareTicketsByQueue;
  }
}

export function TicketsPage() {
  const { token } = useAuth();
  const me = useMe(Boolean(token));
  const [search, setSearch] = useState("");
  const [debouncedSearch] = useDebouncedValue(search, 400);
  const tickets = useTickets({ search: debouncedSearch });
  const bulkUpdate = useBulkUpdateTickets();
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [departmentFilter, setDepartmentFilter] = useState<string | null>(null);
  const [slaFilter, setSlaFilter] = useState<string | null>(null);
  const [sortMode, setSortMode] = useState<SortMode>("smart");
  const [queue, setQueue] = useState<TicketQueue>("active");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkResult, setBulkResult] = useState<TicketBulkResponse | null>(null);

  const role = me.data?.role;
  const isOperator = role === "admin" || role === "agent";
  const isAdmin = role === "admin";

  // Clear selection when queue changes to avoid stale cross-queue selections
  useEffect(() => {
    setSelectedIds(new Set());
  }, [queue]);

  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  async function handleBulkAction(action: TicketBulkAction, force?: boolean) {
    if (selectedIds.size === 0) return;
    try {
      const result = await bulkUpdate.mutateAsync({
        ticket_ids: Array.from(selectedIds),
        action,
        ...(force ? { force: true } : {}),
      });
      setBulkResult(result);
      clearSelection();
    } catch {
      // errors shown by BulkActionBar loading state; API error visible in console
    }
  }
  const queueOptions = isOperator ? OPERATOR_QUEUES : USER_QUEUES;
  const activeQueue =
    queueOptions.some((item) => item.value === queue) ? queue : "active";
  const activeQueueDescription =
    queueOptions.find((item) => item.value === activeQueue)?.description ?? "";
  const title = role === "admin" || role === "agent" ? "Запросы" : "Мои запросы";
  const description =
    role === "admin"
      ? "Все обращения пользователей. Подтвержденные запросы можно взять в работу или закрыть."
      : role === "agent"
        ? "Назначенные вам обращения. Подтвержденные запросы можно взять в работу или закрыть."
        : "Активные и отправленные обращения.";

  const ticketCounts = useMemo(() => {
    const source = tickets.data ?? [];
    return queueOptions.reduce<Record<TicketQueue, number>>((acc, item) => {
      acc[item.value] = source.filter((ticket) => matchesQueue(ticket, item.value)).length;
      return acc;
    }, {} as Record<TicketQueue, number>);
  }, [queueOptions, tickets.data]);

  const visibleTickets = useMemo(() => {
    return tickets.data?.filter((ticket) => {
      const matchesCurrentQueue = matchesQueue(ticket, activeQueue);
      const matchesStatus = !statusFilter || ticket.status === statusFilter;
      const matchesDepartment =
        !departmentFilter || ticket.department === departmentFilter;
      const matchesSla =
        !slaFilter ||
        (slaFilter === "overdue" && ticket.is_sla_breached) ||
        (slaFilter === "active" && ticket.sla_deadline_at && !ticket.is_sla_breached);
      return matchesCurrentQueue && matchesStatus && matchesDepartment && matchesSla;
    }).sort(pickTicketComparator(sortMode));
  }, [activeQueue, departmentFilter, slaFilter, sortMode, statusFilter, tickets.data]);

  const groupedTickets = useMemo(() => {
    const items = visibleTickets ?? [];
    return TICKET_GROUPS.map((group) => ({
      ...group,
      tickets: items.filter((ticket) => getTicketGroupId(ticket) === group.id),
    })).filter((group) => group.tickets.length > 0);
  }, [visibleTickets]);
  const showGroupedTickets =
    isOperator &&
    sortMode === "smart" &&
    activeQueue !== "resolved" &&
    groupedTickets.length > 1;

  const activeCount = tickets.data?.filter(isActiveTicket).length ?? 0;
  const overdueCount =
    tickets.data?.filter((ticket) => isActiveTicket(ticket) && ticket.is_sla_breached)
      .length ?? 0;
  const unassignedCount =
    tickets.data?.filter(
      (ticket) => isActiveTicket(ticket) && ticket.agent_id == null,
    ).length ?? 0;

  const error = tickets.error || me.error;

  return (
    <div className="content-page">
      <Paper className="tickets-panel" withBorder>
        <LoadingOverlay visible={tickets.isLoading || me.isLoading} />
        <Group justify="space-between" align="flex-start" mb={8} wrap="nowrap" gap={8}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <Title order={2} mb={4}>
              {title}
            </Title>
            <Text size="sm" c="dimmed">
              {description}
            </Text>
          </div>
          {isOperator && (
            <Button
              variant="light"
              size="sm"
              leftSection={<IconDownload size={16} />}
              disabled={!visibleTickets || visibleTickets.length === 0}
              onClick={() => {
                if (!visibleTickets?.length) return;
                const today = new Date().toISOString().slice(0, 10);
                // Имя файла включает текущую очередь — чтобы при экспорте
                // нескольких срезов файлы не перетирали друг друга в Downloads.
                downloadTicketsCsv(visibleTickets, `tickets-${activeQueue}-${today}.csv`);
              }}
            >
              Экспорт CSV
            </Button>
          )}
        </Group>

        {isOperator && (
          <SimpleGrid
            className="ticket-queue-summary"
            cols={{ base: 1, sm: 3 }}
            spacing={8}
            mb={8}
          >
            <div className="queue-summary-item">
              <Text size="xs" c="dimmed" fw={600}>
                Активные
              </Text>
              <Text className="queue-summary-value">{activeCount}</Text>
            </div>
            <div className={`queue-summary-item${overdueCount ? " danger" : ""}`}>
              <Text size="xs" c="dimmed" fw={600}>
                Просрочены
              </Text>
              <Text className="queue-summary-value">{overdueCount}</Text>
            </div>
            <div className={`queue-summary-item${unassignedCount ? " warning" : ""}`}>
              <Text size="xs" c="dimmed" fw={600}>
                Без исполнителя
              </Text>
              <Text className="queue-summary-value">{unassignedCount}</Text>
            </div>
          </SimpleGrid>
        )}

        <Tabs
          value={activeQueue}
          onChange={(value) => value && setQueue(value as TicketQueue)}
          mb={6}
        >
          <Tabs.List>
            {queueOptions.map((item) => (
              <Tabs.Tab key={item.value} value={item.value}>
                <Group gap={6} wrap="nowrap">
                  <span>{item.label}</span>
                  <Badge size="xs" variant="light">
                    {ticketCounts[item.value] ?? 0}
                  </Badge>
                </Group>
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>

        <Text size="sm" c="dimmed" mb={8}>
          {activeQueueDescription}
        </Text>

        <Group className="ticket-filters" align="end" mb={8}>
          <TextInput
            label="Поиск"
            placeholder="Тема, заявитель, офис, объект"
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
          />
          <Select
            label="Статус"
            data={STATUS_OPTIONS}
            value={statusFilter}
            clearable
            onChange={setStatusFilter}
          />
          <Select
            label="Отдел"
            data={DEPARTMENT_OPTIONS}
            value={departmentFilter}
            clearable
            onChange={setDepartmentFilter}
          />
          <Select
            label="SLA"
            data={SLA_OPTIONS}
            value={slaFilter}
            clearable
            onChange={setSlaFilter}
          />
          <Select
            label="Сортировка"
            data={SORT_OPTIONS}
            value={sortMode}
            allowDeselect={false}
            onChange={(value) => value && setSortMode(value as SortMode)}
          />
        </Group>

        {error && (
          <Alert color="red" variant="light" mb={8}>
            {getApiError(error)}
          </Alert>
        )}

        {isOperator && visibleTickets && visibleTickets.length > 0 && (
          <Group justify="flex-start" mb="xs">
            <Checkbox
              label={
                selectedIds.size === visibleTickets.length
                  ? "Снять всё"
                  : `Выбрать все (${visibleTickets.length})`
              }
              checked={
                visibleTickets.length > 0 &&
                selectedIds.size === visibleTickets.length
              }
              indeterminate={
                selectedIds.size > 0 &&
                selectedIds.size < visibleTickets.length
              }
              onChange={() => {
                if (selectedIds.size === visibleTickets.length) {
                  clearSelection();
                } else {
                  setSelectedIds(new Set(visibleTickets.map((t) => t.id)));
                }
              }}
            />
          </Group>
        )}

        {!visibleTickets?.length && !tickets.isLoading ? (
          <div className="empty-state tickets">
            <Text fw={600}>
              {tickets.data?.length ? "По фильтрам запросов нет" : "Запросов нет"}
            </Text>
          </div>
        ) : showGroupedTickets ? (
          <Stack gap={8} className="ticket-groups">
            {groupedTickets.map((group) => (
              <section
                key={group.id}
                className={`ticket-group ticket-group-${group.color}`}
              >
                <Group justify="space-between" gap={6} mb={6} align="flex-start">
                  <div>
                    <Group gap="xs">
                      <Text fw={700}>{group.label}</Text>
                      <Badge size="sm" color={group.color} variant="light">
                        {group.tickets.length}
                      </Badge>
                    </Group>
                    <Text size="sm" c="dimmed">
                      {group.description}
                    </Text>
                  </div>
                </Group>
                <SimpleGrid cols={{ base: 1, md: 2 }} spacing={8}>
                  {group.tickets.map((ticket) => (
                    <TicketCard
                      key={ticket.id}
                      ticket={ticket}
                      currentUserRole={me.data?.role}
                      selectable={isOperator}
                      selected={selectedIds.has(ticket.id)}
                      onSelect={toggleSelect}
                    />
                  ))}
                </SimpleGrid>
              </section>
            ))}
          </Stack>
        ) : (
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing={8}>
            {visibleTickets?.map((ticket) => (
              <TicketCard
                key={ticket.id}
                ticket={ticket}
                currentUserRole={me.data?.role}
                selectable={isOperator}
                selected={selectedIds.has(ticket.id)}
                onSelect={toggleSelect}
              />
            ))}
          </SimpleGrid>
        )}

        {isOperator && (
          <BulkActionBar
            selectedCount={selectedIds.size}
            isAdmin={isAdmin}
            loading={bulkUpdate.isPending}
            onAction={handleBulkAction}
            onClear={clearSelection}
          />
        )}
      </Paper>

      <BulkResultModal
        opened={bulkResult !== null}
        onClose={() => setBulkResult(null)}
        applied={bulkResult?.applied_count ?? 0}
        requested={bulkResult?.requested_count ?? 0}
        rejected={bulkResult?.rejected ?? []}
      />
    </div>
  );
}
