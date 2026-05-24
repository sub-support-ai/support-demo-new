import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconArrowRight,
  IconBook,
  IconCheck,
  IconClock,
  IconInbox,
  IconList,
  IconUser,
} from "@tabler/icons-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { useStats } from "../../api/stats";
import { useTickets } from "../../api/tickets";
import type { Ticket, UserMe } from "../../api/types";
import { getDepartmentLabel, getStatusLabel } from "../../lib/ticketLabels";
import { TrendsSection } from "./TrendsSection";

// ─── Утилиты ──────────────────────────────────────────────────────────────

function timeGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 6) return "Доброй ночи";
  if (hour < 12) return "Доброе утро";
  if (hour < 18) return "Добрый день";
  return "Добрый вечер";
}

function withinDays(value: string | null | undefined, days: number): boolean {
  if (!value) return false;
  const ts = new Date(value).getTime();
  if (Number.isNaN(ts)) return false;
  const diffDays = (Date.now() - ts) / (1000 * 3600 * 24);
  return diffDays >= 0 && diffDays <= days;
}

function relativeTime(value: string | null | undefined): string {
  if (!value) return "—";
  const ts = new Date(value).getTime();
  if (Number.isNaN(ts)) return "—";
  const diff = Math.max(0, (Date.now() - ts) / 1000);
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  return `${Math.floor(diff / 86400)} д назад`;
}

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return m > 0 ? `${h} ч ${m} мин` : `${h} ч`;
  if (m > 0) return `${m} мин`;
  return `${Math.round(seconds)} с`;
}

/** SLA: статус и текст. null — дедлайна нет или времени ещё много. */
function slaLabel(
  ticket: Ticket,
): { text: string; tone: "danger" | "warning" } | null {
  if (!ticket.sla_deadline_at) return null;
  const deadline = new Date(ticket.sla_deadline_at).getTime();
  const diffMs = deadline - Date.now();

  if (ticket.is_sla_breached || diffMs < 0) {
    const absMs = Math.abs(diffMs);
    const overdueH = Math.floor(absMs / 3_600_000);
    const overdueM = Math.floor((absMs % 3_600_000) / 60_000);
    const text =
      overdueH > 0 ? `просрочен ${overdueH} ч ${overdueM} мин` : `просрочен ${overdueM} мин`;
    return { text, tone: "danger" };
  }

  const diffH = diffMs / 3_600_000;
  if (diffH < 2) {
    const remainM = Math.floor(diffMs / 60_000);
    return { text: `${remainM} мин до дедлайна`, tone: "warning" };
  }
  if (diffH < 8) {
    return { text: `${Math.floor(diffH)} ч до дедлайна`, tone: "warning" };
  }
  return null;
}

const ACTIVE_STATUSES = new Set(["confirmed", "in_progress", "ai_processing"]);
const RESOLVED_STATUSES = new Set(["resolved", "closed"]);

// ─── Карточка счётчика очереди ────────────────────────────────────────────
// Переиспользует стили .queue-summary-item из styles.css

function QueueCard({
  label,
  value,
  hint,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: number;
  hint?: string;
  tone?: "neutral" | "warning" | "danger";
  icon: React.ReactNode;
}) {
  return (
    <div className={`queue-summary-item${tone !== "neutral" ? ` ${tone}` : ""}`}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Text size="xs" tt="uppercase" fw={700} c="dimmed">
          {label}
        </Text>
        {icon}
      </Group>
      <Text className="queue-summary-value">{value}</Text>
      {hint && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
    </div>
  );
}

// ─── Строка просроченного тикета ──────────────────────────────────────────

function UrgentTicketRow({ ticket }: { ticket: Ticket }) {
  const sla = slaLabel(ticket);
  return (
    <Paper withBorder p="sm" className="urgent-ticket-row">
      <Group justify="space-between" align="center" wrap="nowrap" gap="sm">
        <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
          <Group gap="xs" wrap="nowrap">
            <Badge color="red" variant="filled" size="xs">
              #{ticket.id}
            </Badge>
            <Badge size="xs" variant="light">
              {getDepartmentLabel(ticket.department)}
            </Badge>
            {sla && (
              <Badge
                size="xs"
                color={sla.tone === "danger" ? "red" : "orange"}
                variant="filled"
              >
                {sla.text}
              </Badge>
            )}
          </Group>
          <Text size="sm" fw={500} lineClamp={1}>
            {ticket.title}
          </Text>
          <Text size="xs" c="dimmed">
            {getStatusLabel(ticket.status)} ·{" "}
            {relativeTime(ticket.updated_at ?? ticket.created_at)}
          </Text>
        </Stack>
        <Button
          component={Link}
          to="/tickets"
          variant="light"
          color="red"
          size="xs"
          rightSection={<IconArrowRight size={14} />}
        >
          Взять
        </Button>
      </Group>
    </Paper>
  );
}

// ─── Строка «моего» тикета ────────────────────────────────────────────────

function MyTicketRow({ ticket }: { ticket: Ticket }) {
  const sla = slaLabel(ticket);
  return (
    <Paper className="employee-ticket-row" withBorder p="sm">
      <Group justify="space-between" align="center" wrap="nowrap" gap="sm">
        <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
          <Group gap="xs" wrap="nowrap">
            <Text size="xs" c="dimmed">
              #{ticket.id}
            </Text>
            <Badge size="xs" variant="light">
              {getStatusLabel(ticket.status)}
            </Badge>
            {sla && (
              <Badge
                size="xs"
                color={sla.tone === "danger" ? "red" : "orange"}
                variant="dot"
              >
                {sla.text}
              </Badge>
            )}
          </Group>
          <Text size="sm" fw={500} lineClamp={1}>
            {ticket.title}
          </Text>
        </Stack>
        <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
          {relativeTime(ticket.updated_at ?? ticket.created_at)}
        </Text>
      </Group>
    </Paper>
  );
}

// ─── Главный компонент ────────────────────────────────────────────────────

export function AgentDashboard({ me }: { me: UserMe }) {
  const stats = useStats();
  const tickets = useTickets({ enabled: true, refetchInterval: 20_000 });
  const allTickets: Ticket[] = tickets.data ?? [];
  const statsData = stats.data;

  // ── Производные списки ──────────────────────────────────────────────────

  /** Тикеты с нарушенным SLA или перешедшие дедлайн, ещё не закрытые. */
  const overdueTickets = useMemo(
    () =>
      allTickets
        .filter(
          (t) =>
            (t.is_sla_breached ||
              (t.sla_deadline_at && new Date(t.sla_deadline_at) < new Date())) &&
            !RESOLVED_STATUSES.has(t.status) &&
            t.status !== "declined",
        )
        .sort(
          (a, b) =>
            new Date(a.sla_deadline_at ?? a.created_at).getTime() -
            new Date(b.sla_deadline_at ?? b.created_at).getTime(),
        ),
    [allTickets],
  );

  /** Тикеты, у которых < 2 ч до дедлайна, но SLA ещё не нарушен. */
  const warningSlaTickets = useMemo(
    () =>
      allTickets.filter((t) => {
        if (!t.sla_deadline_at || RESOLVED_STATUSES.has(t.status) || t.is_sla_breached)
          return false;
        const minsLeft = (new Date(t.sla_deadline_at).getTime() - Date.now()) / 60_000;
        return minsLeft > 0 && minsLeft < 120;
      }),
    [allTickets],
  );

  /** Новые тикеты без назначения. */
  const newTickets = useMemo(
    () => allTickets.filter((t) => t.status === "new"),
    [allTickets],
  );

  /** Тикеты, назначенные на меня. */
  const myTickets = useMemo(
    () => (me.agent_id ? allTickets.filter((t) => t.agent_id === me.agent_id) : []),
    [allTickets, me.agent_id],
  );

  /** Активные тикеты в работе у меня, отсортированные по приоритету. */
  const myActive = useMemo(
    () =>
      myTickets
        .filter((t) => ACTIVE_STATUSES.has(t.status))
        .sort((a, b) => {
          const rankA = a.status === "in_progress" ? 0 : a.status === "confirmed" ? 1 : 2;
          const rankB = b.status === "in_progress" ? 0 : b.status === "confirmed" ? 1 : 2;
          if (rankA !== rankB) return rankA - rankB;
          return (
            new Date(b.updated_at ?? b.created_at).getTime() -
            new Date(a.updated_at ?? a.created_at).getTime()
          );
        }),
    [myTickets],
  );

  /** Мои закрытые за последние 7 дней — личная эффективность. */
  const myResolved7d = useMemo(
    () =>
      myTickets.filter(
        (t) =>
          RESOLVED_STATUSES.has(t.status) &&
          withinDays(t.resolved_at ?? t.updated_at, 7),
      ),
    [myTickets],
  );

  const isLoading = tickets.isLoading;

  return (
    <div className="content-page agent-dashboard">
      <Stack gap="lg">
        {/* ─── Hero: приветствие оператора ──────────────────────── */}
        <Paper withBorder p="lg" className="agent-hero">
          <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
            <Stack gap={4}>
              <Group gap="xs" align="center">
                <Title order={2}>
                  {timeGreeting()}, {me.username}
                </Title>
                {me.agent_department && (
                  <Badge variant="outline" color="teal" size="lg">
                    {getDepartmentLabel(me.agent_department)}
                  </Badge>
                )}
              </Group>
              <Text size="sm" c="dimmed">
                {overdueTickets.length > 0
                  ? `${overdueTickets.length} ${
                      overdueTickets.length === 1 ? "тикет просрочен" : "тикетов просрочено"
                    } — нужна немедленная реакция.`
                  : newTickets.length > 0
                    ? `${newTickets.length} новых тикетов ожидают назначения.`
                    : "Очередь в норме — новых критических событий нет."}
              </Text>
            </Stack>
            <Group gap="xs">
              <Button
                component={Link}
                to="/tickets"
                leftSection={<IconInbox size={16} />}
                color="teal"
                variant="filled"
              >
                Очередь
              </Button>
              <Button
                component={Link}
                to="/knowledge"
                leftSection={<IconBook size={16} />}
                variant="light"
              >
                База знаний
              </Button>
            </Group>
          </Group>
        </Paper>

        {/* ─── Счётчики очереди ──────────────────────────────────── */}
        <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="md">
          <QueueCard
            label="Новые / без назначения"
            value={newTickets.length}
            hint="ждут обработки"
            tone={newTickets.length > 5 ? "warning" : "neutral"}
            icon={<IconInbox size={16} color="#868e96" />}
          />
          <QueueCard
            label="SLA просрочен"
            value={overdueTickets.length}
            hint={overdueTickets.length > 0 ? "требуют реакции" : "всё в норме"}
            tone={overdueTickets.length > 0 ? "danger" : "neutral"}
            icon={
              <IconClock
                size={16}
                color={overdueTickets.length > 0 ? "#fa5252" : "#868e96"}
              />
            }
          />
          <QueueCard
            label="Мои в работе"
            value={myActive.length}
            hint={me.agent_id ? "назначено на меня" : "нет agent_id"}
            tone="neutral"
            icon={<IconUser size={16} color="#868e96" />}
          />
          <QueueCard
            label="Решено за 7 дней"
            value={myResolved7d.length}
            hint="мои закрытые"
            tone="neutral"
            icon={<IconCheck size={16} color="#20c997" />}
          />
        </SimpleGrid>

        {/* ─── Загрузка ──────────────────────────────────────────── */}
        {isLoading && (
          <Group justify="center" p="md">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              Загружаем очередь…
            </Text>
          </Group>
        )}

        {/* ─── SLA: просрочены ───────────────────────────────────── */}
        {overdueTickets.length > 0 && (
          <Paper withBorder p="md">
            <Group justify="space-between" mb="sm" align="center">
              <Group gap="xs">
                <ThemeIcon variant="light" color="red" size={28} radius="md">
                  <IconAlertTriangle size={16} />
                </ThemeIcon>
                <Title order={4}>Просрочен SLA</Title>
              </Group>
              <Badge color="red" variant="filled">
                {overdueTickets.length}
              </Badge>
            </Group>
            <Stack gap="xs">
              {overdueTickets.slice(0, 5).map((t) => (
                <UrgentTicketRow key={t.id} ticket={t} />
              ))}
              {overdueTickets.length > 5 && (
                <Button
                  component={Link}
                  to="/tickets"
                  variant="subtle"
                  size="xs"
                  rightSection={<IconArrowRight size={14} />}
                >
                  Ещё {overdueTickets.length - 5} просроченных…
                </Button>
              )}
            </Stack>
          </Paper>
        )}

        {/* ─── SLA: предупреждение (< 2 ч до дедлайна) ─────────── */}
        {warningSlaTickets.length > 0 && (
          <Alert
            color="orange"
            variant="light"
            icon={<IconClock size={18} />}
            title={`${warningSlaTickets.length} тикет${
              warningSlaTickets.length === 1 ? "" : "ов"
            } почти просрочен${warningSlaTickets.length === 1 ? "" : "о"}`}
          >
            <Text size="sm">
              Менее 2 часов до дедлайна:{" "}
              {warningSlaTickets
                .slice(0, 3)
                .map((t) => `#${t.id} «${t.title.slice(0, 30)}${t.title.length > 30 ? "…" : ""}»`)
                .join(", ")}
              {warningSlaTickets.length > 3 && ` и ещё ${warningSlaTickets.length - 3}`}.
            </Text>
          </Alert>
        )}

        {/* ─── Мои тикеты ────────────────────────────────────────── */}
        {me.agent_id && (
          <Paper withBorder p="md">
            <Group justify="space-between" mb="sm" align="center">
              <Group gap="xs">
                <ThemeIcon variant="light" color="blue" size={28} radius="md">
                  <IconList size={16} />
                </ThemeIcon>
                <Title order={4}>Мои тикеты</Title>
              </Group>
              <Button
                component={Link}
                to="/tickets"
                variant="subtle"
                size="xs"
                rightSection={<IconArrowRight size={14} />}
              >
                Все
              </Button>
            </Group>
            {myActive.length > 0 ? (
              <Stack gap="xs">
                {myActive.slice(0, 6).map((t) => (
                  <MyTicketRow key={t.id} ticket={t} />
                ))}
              </Stack>
            ) : (
              <Text size="sm" c="dimmed">
                Нет активных тикетов, назначенных на вас.
              </Text>
            )}
          </Paper>
        )}

        {/* ─── Тренды по тикетам ──────────────────────────────────
            Динамика создания/решения по дням за выбранный период.
            Для агента — главный сигнал: нагрузка растёт или падает,
            справляемся ли мы (resolved ≈ created) или копится backlog. */}
        <TrendsSection />

        {/* ─── Метрики системы ───────────────────────────────────── */}
        {statsData && (
          <Paper withBorder p="md">
            <Title order={4} mb="md">
              Метрики системы
            </Title>
            <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="md">
              <Stack gap={2}>
                <Text size="xs" tt="uppercase" fw={700} c="dimmed">
                  TTFR (среднее)
                </Text>
                <Text fw={700} fz={22}>
                  {statsData.tickets.avg_ttfr_seconds != null
                    ? formatDuration(statsData.tickets.avg_ttfr_seconds)
                    : "—"}
                </Text>
                <Text size="xs" c="dimmed">
                  время первого ответа
                </Text>
              </Stack>
              <Stack gap={2}>
                <Text size="xs" tt="uppercase" fw={700} c="dimmed">
                  TTR (среднее)
                </Text>
                <Text fw={700} fz={22}>
                  {statsData.tickets.avg_ttr_seconds != null
                    ? formatDuration(statsData.tickets.avg_ttr_seconds)
                    : "—"}
                </Text>
                <Text size="xs" c="dimmed">
                  время решения
                </Text>
              </Stack>
              <Stack gap={2}>
                <Text size="xs" tt="uppercase" fw={700} c="dimmed">
                  Маршрутизация
                </Text>
                <Text fw={700} fz={22}>
                  {Math.round(statsData.ai.routing_accuracy_pct)}%
                </Text>
                <Text size="xs" c="dimmed">
                  подтверждено агентами
                </Text>
              </Stack>
              <Stack gap={2}>
                <Text size="xs" tt="uppercase" fw={700} c="dimmed">
                  AI решил сам
                </Text>
                <Text fw={700} fz={22} c="teal">
                  {statsData.ai.resolved_by_ai_count}
                </Text>
                <Text size="xs" c="dimmed">
                  без эскалации
                </Text>
              </Stack>
            </SimpleGrid>
          </Paper>
        )}
      </Stack>
    </div>
  );
}
