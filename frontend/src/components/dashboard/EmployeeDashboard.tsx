import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  Progress,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconArrowRight,
  IconCheck,
  IconChevronLeft,
  IconChevronRight,
  IconFileText,
  IconMessageCircle,
  IconPlus,
  IconRobot,
  IconSparkles,
} from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useConversations } from "../../api/conversations";
import { useTickets } from "../../api/tickets";
import type { Conversation, Ticket, UserMe } from "../../api/types";
import {
  formatFriendlyDeadline,
  getDeadlineColor,
  getDeadlineStatus,
} from "../../lib/sla";
import { getDepartmentLabel, getStatusLabel } from "../../lib/ticketLabels";

const INTAKE_FIELD_LABELS: Record<string, string> = {
  requester_name: "имя заявителя",
  requester_email: "рабочий email",
  office: "офис или кабинет",
  affected_item: "что затронуто",
  symptoms: "что происходит",
  business_impact: "как это мешает работе",
  what_tried: "что уже пробовали",
  what_user_did: "что уже сделали",
  time_detected: "когда заметили проблему",
  asset_id: "инвентарный номер",
  asset_type: "тип оборудования",
};

function formatMissingFields(fields: string[] | undefined): string {
  if (!fields?.length) return "уточнений нет";
  return fields
    .map((field) => INTAKE_FIELD_LABELS[field] ?? field.replace(/_/g, " "))
    .join(", ");
}

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

/** Попадает ли дата в указанный календарный месяц (год + месяц 1–12). */
function withinMonth(
  value: string | null | undefined,
  year: number,
  month: number,
): boolean {
  if (!value) return false;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return false;
  return d.getFullYear() === year && d.getMonth() + 1 === month;
}

/** «Май 2026» с заглавной буквы. */
function formatMonthTitle(year: number, month: number): string {
  const s = new Date(year, month - 1, 1).toLocaleDateString("ru-RU", {
    month: "long",
    year: "numeric",
  });
  return s.charAt(0).toUpperCase() + s.slice(1);
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

const ACTIVE_STATUSES = ["confirmed", "in_progress", "ai_processing"];
const RESOLVED_STATUSES = ["resolved", "closed"];

// ─── Карточка статистики ──────────────────────────────────────────────────

function QuickStat({
  label,
  value,
  hint,
  icon,
  color,
}: {
  label: string;
  value: number | string;
  hint?: string;
  icon: React.ReactNode;
  color: string;
}) {
  return (
    <Paper className="employee-stat-card" withBorder p="md">
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Stack gap={4}>
          <Text size="xs" tt="uppercase" fw={700} c="dimmed">
            {label}
          </Text>
          <Text fz={32} fw={700} lh={1}>
            {value}
          </Text>
          {hint && (
            <Text size="xs" c="dimmed">
              {hint}
            </Text>
          )}
        </Stack>
        <ThemeIcon size={40} radius="md" variant="light" color={color}>
          {icon}
        </ThemeIcon>
      </Group>
    </Paper>
  );
}

// ─── Карточка тикета (компактная) ─────────────────────────────────────────

function TicketRow({ ticket }: { ticket: Ticket }) {
  // SLA для сотрудника: «когда мне ответят» — абсолютная точка времени.
  // Показываем только если есть deadline и тикет не закрыт. Для уверенности
  // фильтр статусов — на стороне родителя (мы здесь рендерим только активные),
  // но дополнительная проверка не повредит при будущем переиспользовании.
  const deadlineStatus = getDeadlineStatus(ticket.sla_deadline_at, {
    breached: ticket.is_sla_breached,
  });
  const deadlineText = formatFriendlyDeadline(ticket.sla_deadline_at, {
    breached: ticket.is_sla_breached,
  });

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
            <Badge size="xs" variant="outline">
              {getDepartmentLabel(ticket.department)}
            </Badge>
            {deadlineStatus && deadlineText && (
              <Badge
                size="xs"
                color={getDeadlineColor(deadlineStatus)}
                variant={deadlineStatus === "breached" ? "filled" : "light"}
              >
                {deadlineText}
              </Badge>
            )}
          </Group>
          <Text size="sm" lineClamp={1} fw={500}>
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

// ─── Карточка «нужно действие» ────────────────────────────────────────────

function AttentionCard({
  title,
  subtitle,
  badge,
  badgeColor,
  to,
  ctaLabel,
}: {
  title: string;
  subtitle: string;
  badge: string;
  badgeColor: string;
  to: string;
  ctaLabel: string;
}) {
  return (
    <Paper className="attention-card" withBorder p="sm">
      <Group justify="space-between" align="center" wrap="nowrap" gap="sm">
        <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
          <Group gap="xs">
            <Badge color={badgeColor} variant="filled" size="sm">
              {badge}
            </Badge>
            <Text size="sm" fw={600} lineClamp={1}>
              {title}
            </Text>
          </Group>
          <Text size="xs" c="dimmed" lineClamp={2}>
            {subtitle}
          </Text>
        </Stack>
        <Button
          component={Link}
          to={to}
          variant="light"
          size="xs"
          rightSection={<IconArrowRight size={14} />}
        >
          {ctaLabel}
        </Button>
      </Group>
    </Paper>
  );
}

// ─── Главный компонент ────────────────────────────────────────────────────

export function EmployeeDashboard({ me }: { me: UserMe }) {
  const tickets = useTickets({ enabled: true, refetchInterval: 30000 });
  const conversations = useConversations();

  const allTickets: Ticket[] = tickets.data ?? [];
  const allConversations: Conversation[] = conversations.data ?? [];

  // ── Навигация по месяцам для «Ваш месяц» ───────────────────────────────
  const today = new Date();
  const [monthYear, setMonthYear] = useState(() => ({
    year: today.getFullYear(),
    month: today.getMonth() + 1,
  }));
  const { year: selYear, month: selMonth } = monthYear;
  const isCurrentMonth =
    selYear === today.getFullYear() && selMonth === today.getMonth() + 1;
  const minYear = today.getFullYear() - 2;
  const minMonth = today.getMonth() + 1;
  const isMinMonth =
    selYear < minYear || (selYear === minYear && selMonth <= minMonth);

  function prevMonth() {
    if (isMinMonth) return;
    setMonthYear(({ year, month }) =>
      month === 1 ? { year: year - 1, month: 12 } : { year, month: month - 1 },
    );
  }
  function nextMonth() {
    if (isCurrentMonth) return;
    setMonthYear(({ year, month }) =>
      month === 12 ? { year: year + 1, month: 1 } : { year, month: month + 1 },
    );
  }

  // ── Категоризация тикетов ───────────────────────────────────────────────
  const draftTickets = useMemo(
    () =>
      allTickets.filter(
        (t) => t.status === "pending_user" && !t.confirmed_by_user,
      ),
    [allTickets],
  );

  const activeTickets = useMemo(
    () => allTickets.filter((t) => ACTIVE_STATUSES.includes(t.status)),
    [allTickets],
  );

  const resolvedRecent = useMemo(
    () =>
      allTickets.filter(
        (t) =>
          RESOLVED_STATUSES.includes(t.status) &&
          withinMonth(t.resolved_at ?? t.updated_at, selYear, selMonth),
      ),
    [allTickets, selYear, selMonth],
  );

  const createdRecent = useMemo(
    () => allTickets.filter((t) => withinMonth(t.created_at, selYear, selMonth)),
    [allTickets, selYear, selMonth],
  );

  // ── Прогресс за месяц ───────────────────────────────────────────────────
  const monthlyTotal = createdRecent.length;
  const monthlyResolved = resolvedRecent.length;
  const progressPct =
    monthlyTotal > 0
      ? Math.min(100, Math.round((monthlyResolved / monthlyTotal) * 100))
      : 0;

  // ── «AI решает за вас» — считаем по тикетам с ticket_source="ai_generated" ──
  // Это наш уникальный показатель: сколько обращений закрыто без эскалации
  // на специалиста благодаря AI и базе знаний.
  const aiResolvedCount = useMemo(
    () =>
      resolvedRecent.filter(
        (t) => t.ticket_source === "ai_generated" && t.ai_processed_at,
      ).length,
    [resolvedRecent],
  );

  // ── Активные диалоги, где AI ждёт ответа ────────────────────────────────
  // Показываем только те, к которым ещё НЕТ pending_user черновика —
  // иначе сообщение будет дублироваться (черновик уже зовёт к подтверждению).
  const draftConversationIds = useMemo(
    () => new Set(draftTickets.map((t) => t.conversation_id).filter(Boolean)),
    [draftTickets],
  );

  const intakeWaiting = useMemo(
    () =>
      allConversations.filter(
        (c) =>
          c.status === "active" &&
          c.intake_state?.last_question &&
          (c.intake_state?.missing_fields?.length ?? 0) > 0 &&
          !draftConversationIds.has(c.id),
      ),
    [allConversations, draftConversationIds],
  );

  const isLoading = tickets.isLoading || conversations.isLoading;
  const totalAttention = draftTickets.length + intakeWaiting.length;

  // ── Сортируем активные обращения: in_progress → confirmed → ai_processing ──
  const STATUS_RANK: Record<string, number> = {
    in_progress: 0,
    confirmed: 1,
    ai_processing: 2,
  };
  const activeSorted = useMemo(
    () =>
      [...activeTickets].sort((a, b) => {
        const rA = STATUS_RANK[a.status] ?? 9;
        const rB = STATUS_RANK[b.status] ?? 9;
        if (rA !== rB) return rA - rB;
        const tA = new Date(a.updated_at ?? a.created_at).getTime();
        const tB = new Date(b.updated_at ?? b.created_at).getTime();
        return tB - tA;
      }),
    [activeTickets],
  );

  return (
    <div className="content-page employee-dashboard">
      <Stack gap="lg">
        {/* ─── Hero: приветствие ──────────────────────────────────────── */}
        <Paper className="employee-hero" withBorder p="lg">
          <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
            <Stack gap={4}>
              <Title order={1} fz={32} fw={800} lh={1.1}>
                {timeGreeting()}, {me.username}!
              </Title>
              <Text size="sm" c="dimmed">
                {activeTickets.length > 0
                  ? `У вас ${activeTickets.length} ${activeTickets.length === 1 ? "активное обращение" : "активных обращений"} в работе.`
                  : "Сейчас активных обращений нет."}
              </Text>
            </Stack>
            <Group gap="xs">
              <Button
                component={Link}
                to="/chat"
                leftSection={<IconMessageCircle size={16} />}
                variant="filled"
              >
                Открыть AI-чат
              </Button>
              <Button
                component={Link}
                to="/tickets"
                leftSection={<IconFileText size={16} />}
                variant="light"
              >
                Все обращения
              </Button>
            </Group>
          </Group>
        </Paper>

        {/* ─── Быстрая статистика ────────────────────────────────────── */}
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
          <QuickStat
            label="Активные"
            value={activeTickets.length}
            hint="в работе у специалистов"
            icon={<IconFileText size={20} />}
            color="blue"
          />
          <QuickStat
            label="Черновики"
            value={draftTickets.length}
            hint={draftTickets.length > 0 ? "ждут подтверждения" : "всё подтверждено"}
            icon={<IconAlertCircle size={20} />}
            color={draftTickets.length > 0 ? "orange" : "gray"}
          />
          <QuickStat
            label="Решено за месяц"
            value={monthlyResolved}
            hint={
              aiResolvedCount > 0
                ? `${aiResolvedCount} — без участия специалиста`
                : "за последние 30 дней"
            }
            icon={<IconCheck size={20} />}
            color="teal"
          />
        </SimpleGrid>

        {/* ─── Загрузка ─────────────────────────────────────────────── */}
        {isLoading && (
          <Group justify="center" p="md">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              Загружаем ваши обращения…
            </Text>
          </Group>
        )}

        {/* ─── Ожидают вашего ответа ────────────────────────────────── */}
        {totalAttention > 0 && (
          <Paper className="quiet-panel dashboard-section" withBorder p="md">
            <Group justify="space-between" mb="sm" align="center">
              <Group gap="xs">
                <ThemeIcon variant="light" color="orange" size={28} radius="md">
                  <IconAlertCircle size={16} />
                </ThemeIcon>
                <Title order={4}>Ожидают вашего ответа</Title>
              </Group>
              <Badge color="orange" variant="filled">
                {totalAttention}
              </Badge>
            </Group>

            <Stack gap="xs">
              {/* Черновики от AI — нужно подтвердить или изменить */}
              {draftTickets.map((ticket) => (
                <AttentionCard
                  key={`draft-${ticket.id}`}
                  badge="Черновик готов"
                  badgeColor="teal"
                  title={ticket.title || `Обращение #${ticket.id}`}
                  subtitle={`AI собрал данные · отдел ${getDepartmentLabel(ticket.department)} · проверьте и подтвердите отправку`}
                  to="/chat"
                  ctaLabel="Открыть"
                />
              ))}
              {/* Активные диалоги, где AI задал вопрос */}
              {intakeWaiting.map((conv) => (
                <AttentionCard
                  key={`intake-${conv.id}`}
                  badge="AI ждёт ответа"
                  badgeColor="blue"
                  title={conv.intake_state?.last_question || "AI задал уточняющий вопрос"}
                  subtitle={`Не хватает данных: ${formatMissingFields(conv.intake_state?.missing_fields)}`}
                  to="/chat"
                  ctaLabel="Ответить"
                />
              ))}
            </Stack>
          </Paper>
        )}

        {/* ─── Прогресс за месяц ────────────────────────────────────── */}
        <Paper className="quiet-panel dashboard-section" withBorder p="md">
          <Group justify="space-between" align="flex-start" mb="sm">
            <div>
              <Title order={4}>Ваш месяц</Title>
              <Text size="sm" c="dimmed">
                {monthlyTotal > 0
                  ? `Закрыто ${monthlyResolved} из ${monthlyTotal} обращений`
                  : "Обращений в этом месяце нет"}
              </Text>
            </div>
            <Stack gap={4} align="flex-end">
              <Group gap={4} align="center">
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  size="sm"
                  onClick={prevMonth}
                  disabled={isMinMonth}
                  aria-label="Предыдущий месяц"
                >
                  <IconChevronLeft size={14} stroke={1.5} />
                </ActionIcon>
                <Text fw={600} w={110} ta="center" size="sm">
                  {formatMonthTitle(selYear, selMonth)}
                </Text>
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  size="sm"
                  onClick={nextMonth}
                  disabled={isCurrentMonth}
                  aria-label="Следующий месяц"
                >
                  <IconChevronRight size={14} stroke={1.5} />
                </ActionIcon>
              </Group>
              {monthlyTotal > 0 && (
                <Text fz={28} fw={700} c={progressPct >= 70 ? "teal" : "blue"}>
                  {progressPct}%
                </Text>
              )}
            </Stack>
          </Group>
          {monthlyTotal > 0 && (
            <Progress
              value={progressPct}
              size="lg"
              radius="sm"
              color={progressPct >= 70 ? "teal" : "blue"}
            />
          )}
        </Paper>

        {/* ─── Активные обращения ──────────────────────────────────── */}
        {activeSorted.length > 0 && (
          <Paper className="quiet-panel dashboard-section" withBorder p="md">
            <Group justify="space-between" mb="sm" align="center">
              <Title order={4}>Активные обращения</Title>
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
            <Stack gap="xs">
              {activeSorted.slice(0, 5).map((t) => (
                <TicketRow key={t.id} ticket={t} />
              ))}
            </Stack>
          </Paper>
        )}

        {/* ─── AI-помощник (наш дифференциатор) ─────────────────────── */}
        <Alert
          color="violet"
          variant="light"
          icon={<IconSparkles size={18} />}
          title="AI-помощник работает рядом с вами"
        >
          <Group justify="space-between" align="center" wrap="wrap" gap="md">
            <Text size="sm">
              Опишите проблему обычными словами — AI поймёт суть, найдёт ответ в
              базе знаний и при необходимости подготовит обращение в нужный отдел.
              {aiResolvedCount > 0 && (
                <>
                  {" "}
                  <Text component="span" fw={700} c="violet">
                    За месяц AI решил {aiResolvedCount}{" "}
                    {aiResolvedCount === 1
                      ? "ваше обращение"
                      : aiResolvedCount < 5
                        ? "ваших обращения"
                        : "ваших обращений"}{" "}
                    без эскалации.
                  </Text>
                </>
              )}
            </Text>
            <Button
              component={Link}
              to="/chat"
              size="xs"
              variant="white"
              color="violet"
              leftSection={<IconPlus size={14} />}
            >
              Новое обращение
            </Button>
          </Group>
        </Alert>

        {/* ─── Пустое состояние ─────────────────────────────────────── */}
        {!isLoading && activeTickets.length === 0 && totalAttention === 0 && (
          <Paper className="quiet-panel dashboard-section" withBorder p="xl">
            <Stack align="center" gap="sm">
              <ThemeIcon variant="light" color="teal" size={48} radius="xl">
                <IconRobot size={28} />
              </ThemeIcon>
              <Title order={4} ta="center">
                Сейчас всё спокойно
              </Title>
              <Text size="sm" c="dimmed" ta="center" maw={400}>
                У вас нет активных обращений. Если что-то понадобится — просто
                напишите AI-помощнику, он соберёт контекст и поможет.
              </Text>
              <Button
                component={Link}
                to="/chat"
                leftSection={<IconMessageCircle size={16} />}
                mt="xs"
              >
                Написать AI-помощнику
              </Button>
            </Stack>
          </Paper>
        )}
      </Stack>
    </div>
  );
}
