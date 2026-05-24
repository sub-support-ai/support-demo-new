import {
  Alert,
  Badge,
  Button,
  Collapse,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
  UnstyledButton,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconFileText,
  IconSparkles,
  IconX,
} from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { IntakeState, Ticket, TicketDraftUpdate, UserMe } from "../../api/types";
import {
  getCategoryLabel,
  getDepartmentLabel,
  getStatusLabel,
  getTicketPriorityLabel,
} from "../../lib/ticketLabels";
import { validateEmail } from "../../lib/validation";

const DEPARTMENT_OPTIONS = [
  { value: "IT", label: "ИТ" },
  { value: "HR", label: "Кадры" },
  { value: "finance", label: "Финансы" },
  { value: "procurement", label: "Закупки" },
  { value: "security", label: "Безопасность" },
  { value: "facilities", label: "Офис и помещения" },
  { value: "documents", label: "Документооборот" },
];

const PRIORITY_OPTIONS = [
  { value: "низкий", label: "Низкий" },
  { value: "средний", label: "Средний" },
  { value: "высокий", label: "Высокий" },
];

const CRITICAL_PRIORITY_OPTION = {
  value: "критический",
  label: "Критический (системно)",
  disabled: true,
};

// 1.2s — баланс между «не дёргать сервер на каждый keystroke» и «не терять данные».
const AUTOSAVE_DEBOUNCE_MS = 1200;
// «✓ Сохранено» исчезает через 1.8s — достаточно, чтобы заметить, но без визуального мусора.
const SAVED_INDICATOR_FADE_MS = 1800;

type SaveStatus = "idle" | "pending" | "saved" | "error";

function normalizePriority(value?: string | null) {
  const normalized = value?.toLowerCase();
  if (normalized === CRITICAL_PRIORITY_OPTION.value) {
    return CRITICAL_PRIORITY_OPTION.value;
  }
  return PRIORITY_OPTIONS.some((option) => option.value === normalized)
    ? normalized
    : "средний";
}

/** Берём значение из тикета или fallback на intake_state.fields. */
function intakeValue(
  field: string,
  ticketVal: string | null | undefined,
  intakeFields?: Record<string, string | null | undefined> | null,
): string {
  const v = (ticketVal ?? "").trim();
  if (v) return v;
  return ((intakeFields ?? {})[field] ?? "").trim();
}

/** Индикатор статуса сохранения справа сверху. */
function SaveIndicator({ status }: { status: SaveStatus }) {
  if (status === "idle") return null;
  if (status === "pending") {
    return (
      <Group gap={6} align="center">
        <Loader size={12} />
        <Text size="xs" c="dimmed">
          Сохранение…
        </Text>
      </Group>
    );
  }
  if (status === "saved") {
    return (
      <Group gap={4} align="center">
        <IconCheck size={14} color="var(--mantine-color-teal-6)" />
        <Text size="xs" c="teal">
          Сохранено
        </Text>
      </Group>
    );
  }
  return (
    <Group gap={4} align="center">
      <IconAlertTriangle size={14} color="var(--mantine-color-red-6)" />
      <Text size="xs" c="red">
        Не удалось сохранить
      </Text>
    </Group>
  );
}

export function PrefilledTicketPanel({
  ticket,
  intakeState,
  me,
  potentialDuplicates,
  confirmLoading,
  declineLoading,
  saveLoading,
  onConfirm,
  onDecline,
  onSave,
}: {
  ticket: Ticket;
  intakeState?: IntakeState | null;
  me?: UserMe | null;
  potentialDuplicates?: Ticket[];
  confirmLoading?: boolean;
  declineLoading?: boolean;
  saveLoading?: boolean;
  onConfirm: () => void;
  onDecline: () => void;
  onSave: (payload: TicketDraftUpdate) => Promise<void>;
}) {
  const intakeFields = intakeState?.fields ?? null;

  function profileName() {
    return me?.request_context?.requester_name || me?.username || "";
  }
  function profileEmail() {
    return me?.request_context?.requester_email || me?.email || "";
  }

  // Состояние полей — всегда редактируемое, отдельные «view»-режимы нет.
  const [title, setTitle] = useState(ticket.title);
  const [body, setBody] = useState(ticket.body);
  const [department, setDepartment] = useState(ticket.department);
  const [priority, setPriority] = useState(normalizePriority(ticket.ai_priority));
  const [requesterName, setRequesterName] = useState(
    intakeValue("requester_name", ticket.requester_name, intakeFields) || profileName(),
  );
  const [requesterEmail, setRequesterEmail] = useState(
    intakeValue("requester_email", ticket.requester_email, intakeFields) || profileEmail(),
  );
  const [office, setOffice] = useState(
    intakeValue("office", ticket.office, intakeFields),
  );
  const [affectedItem, setAffectedItem] = useState(
    intakeValue("affected_item", ticket.affected_item, intakeFields),
  );
  const [requestType, setRequestType] = useState(ticket.request_type ?? "");
  const [requestDetails, setRequestDetails] = useState(ticket.request_details ?? "");
  const [stepsTried, setStepsTried] = useState(ticket.steps_tried ?? "");

  // Свёрнутая секция «Дополнительно» — раскрыта только если уже что-то заполнено.
  const [extraOpen, setExtraOpen] = useState(
    Boolean((ticket.request_type ?? "") || (ticket.request_details ?? "") || (ticket.steps_tried ?? "")),
  );

  // Статус последнего auto-save — для индикатора в шапке.
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");

  // Refs на обязательные контактные поля — для auto-focus на первое пустое.
  const officeRef = useRef<HTMLInputElement>(null);
  const affectedItemRef = useRef<HTMLInputElement>(null);

  const canEdit = !ticket.confirmed_by_user && ticket.status === "pending_user";
  const requesterEmailError = validateEmail(requesterEmail);
  const missingRequiredLabels: string[] = [];
  if (!office.trim()) missingRequiredLabels.push("офис");
  if (!affectedItem.trim()) missingRequiredLabels.push("что затронуто");
  // Имя и email берём из профиля пользователя — не требуем ручного ввода.
  if (!requesterName.trim()) missingRequiredLabels.push("заявитель");
  if (!requesterEmail.trim() || requesterEmailError) missingRequiredLabels.push("email");
  const hasRequiredContext = missingRequiredLabels.length === 0;
  const canSubmit =
    title.trim().length > 0 && body.trim().length > 0 && hasRequiredContext;
  const isCriticalPriority = priority === CRITICAL_PRIORITY_OPTION.value;

  // Snapshot текущего сохранённого состояния — чтобы не auto-save'ить, если ничего не изменилось.
  // Сравнение через JSON.stringify нормализованного объекта работает быстро для ~10 полей.
  const buildPayload = (): TicketDraftUpdate => {
    const payload: TicketDraftUpdate = {
      title: title.trim(),
      body: body.trim(),
      department: department as TicketDraftUpdate["department"],
      requester_name: requesterName.trim(),
      requester_email: requesterEmail.trim(),
      office: office.trim() || null,
      affected_item: affectedItem.trim() || null,
      request_type: requestType.trim() || null,
      request_details: requestDetails.trim() || null,
      steps_tried: stepsTried.trim() || null,
    };
    if (!isCriticalPriority) {
      payload.ai_priority = priority as "низкий" | "средний" | "высокий";
    }
    return payload;
  };

  // Baseline — последний удачно сохранённый payload. Меняется когда:
  //   1) тикет приходит с сервера (useEffect ниже)
  //   2) auto-save успешно завершается
  const lastSavedRef = useRef<string>("");

  // Сброс полей только при смене тикета (новый id). При обновлении пропа из-за
  // нашего же auto-save сбрасывать нельзя — пользователь продолжает печатать,
  // и сброс выкидывает его незавершённый ввод.
  const prevTicketIdRef = useRef(ticket.id);
  useEffect(() => {
    if (prevTicketIdRef.current === ticket.id) return;
    prevTicketIdRef.current = ticket.id;
    const fields = intakeState?.fields ?? null;
    setTitle(ticket.title);
    setBody(ticket.body);
    setDepartment(ticket.department);
    setPriority(normalizePriority(ticket.ai_priority));
    setRequesterName(intakeValue("requester_name", ticket.requester_name, fields) || profileName());
    setRequesterEmail(intakeValue("requester_email", ticket.requester_email, fields) || profileEmail());
    setOffice(intakeValue("office", ticket.office, fields));
    setAffectedItem(intakeValue("affected_item", ticket.affected_item, fields));
    setRequestType(ticket.request_type ?? "");
    setRequestDetails(ticket.request_details ?? "");
    setStepsTried(ticket.steps_tried ?? "");
    setSaveStatus("idle");
    lastSavedRef.current = "";
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticket.id]);

  // Auto-focus на первое пустое обязательное поле при открытии черновика.
  useEffect(() => {
    if (!canEdit) return;
    const targets = [
      { value: office, ref: officeRef },
      { value: affectedItem, ref: affectedItemRef },
    ];
    const firstEmpty = targets.find((t) => !t.value.trim());
    if (firstEmpty?.ref.current) {
      firstEmpty.ref.current.focus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticket.id]);

  // Auto-save с debounce. Срабатывает только если:
  //   - редактирование разрешено (canEdit)
  //   - payload отличается от baseline
  //   - валидация контактных данных пройдена (иначе сохраняем мусор, но это уже backend'у решать)
  // Сравнение через JSON.stringify нормализованного объекта.
  useEffect(() => {
    if (!canEdit) return;
    const payload = buildPayload();
    const snapshot = JSON.stringify(payload);

    // Первый раз после mount/смены тикета — записываем baseline без отправки.
    if (lastSavedRef.current === "") {
      lastSavedRef.current = snapshot;
      return;
    }
    if (snapshot === lastSavedRef.current) return;

    setSaveStatus("pending");
    const handle = window.setTimeout(async () => {
      try {
        await onSave(payload);
        lastSavedRef.current = snapshot;
        setSaveStatus("saved");
        window.setTimeout(() => {
          // Если за время fade пользователь снова изменил поля — индикатор «pending» уже стоит,
          // не сбрасываем.
          setSaveStatus((curr) => (curr === "saved" ? "idle" : curr));
        }, SAVED_INDICATOR_FADE_MS);
      } catch {
        setSaveStatus("error");
      }
    }, AUTOSAVE_DEBOUNCE_MS);

    return () => window.clearTimeout(handle);
    // Зависим от всех полей, которые попадают в payload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    title,
    body,
    department,
    priority,
    requesterName,
    requesterEmail,
    office,
    affectedItem,
    requestType,
    requestDetails,
    stepsTried,
    canEdit,
  ]);

  // Перед confirm — если есть несохранённый payload, дофлашим его синхронно.
  // Иначе backend может подтвердить тикет со старыми данными.
  async function handleConfirmWithFlush() {
    const payload = buildPayload();
    const snapshot = JSON.stringify(payload);
    if (snapshot !== lastSavedRef.current && canEdit) {
      try {
        setSaveStatus("pending");
        await onSave(payload);
        lastSavedRef.current = snapshot;
        setSaveStatus("saved");
      } catch {
        setSaveStatus("error");
        return; // не подтверждаем если не смогли сохранить
      }
    }
    onConfirm();
  }

  const departmentLabel = getDepartmentLabel(department);
  const departmentDisplayLabel = useMemo(
    () => DEPARTMENT_OPTIONS.find((opt) => opt.value === department)?.label ?? department,
    [department],
  );

  return (
    <Paper withBorder p={8} radius="md" className="draft-panel">
      <Stack gap={6}>
        {/* ── Шапка: только название и индикатор. Статус-badge показываем только
            для НЕ-дефолтных состояний (pending_user — это «в процессе», тривиально). ── */}
        <Group justify="space-between" align="center" wrap="nowrap">
          <Group gap="xs" align="center">
            <IconFileText size={18} color="var(--mantine-color-teal-6)" />
            <Text fw={600} size="sm">
              Черновик запроса
            </Text>
            {ticket.status !== "pending_user" && (
              <Badge size="sm" variant="light">
                {getStatusLabel(ticket.status)}
              </Badge>
            )}
          </Group>
          {canEdit ? (
            <SaveIndicator status={saveStatus} />
          ) : (
            <Badge size="sm" variant="filled" color="teal">
              Подтверждено
            </Badge>
          )}
        </Group>

        {/* ── Полоска AI-классификации: показывает, что заявку разобрал AI-диспетчер ── */}
        {(getCategoryLabel(ticket.ai_category) ||
          (typeof ticket.ai_confidence === "number" && ticket.ai_confidence > 0)) && (
          <Group gap={6} align="center" className="ai-classification-strip" wrap="wrap">
            <IconSparkles size={14} color="var(--mantine-color-violet-6)" />
            <Text size="xs" c="dimmed">
              AI распознал
            </Text>
            {getCategoryLabel(ticket.ai_category) && (
              <Badge size="xs" variant="light" color="violet">
                {getCategoryLabel(ticket.ai_category)}
              </Badge>
            )}
            {typeof ticket.ai_confidence === "number" && ticket.ai_confidence > 0 && (
              <Text size="xs" c="dimmed">
                · уверенность {Math.round(ticket.ai_confidence * 100)}%
              </Text>
            )}
          </Group>
        )}

        {/* ── Title ── */}
        <div>
          {canEdit ? (
            <Textarea
              variant="unstyled"
              autosize
              minRows={1}
              maxRows={3}
              value={title}
              maxLength={255}
              placeholder="Краткий заголовок проблемы"
              classNames={{ input: "draft-title-input" }}
              onChange={(event) => setTitle(event.currentTarget.value)}
            />
          ) : (
            <Text fw={600} size="lg">
              {title || "—"}
            </Text>
          )}
        </div>

        {/* ── Body ── */}
        <div>
          <Text size="xs" c="dimmed" fw={600} mb={2}>
            ОПИСАНИЕ ДЛЯ АГЕНТА
          </Text>
          {canEdit ? (
            <Textarea
              variant="unstyled"
              autosize
              minRows={2}
              maxRows={10}
              value={body}
              placeholder="Опишите проблему максимально подробно"
              classNames={{ input: "draft-body-input" }}
              onChange={(event) => setBody(event.currentTarget.value)}
            />
          ) : (
            <Text size="sm" className="draft-body-readonly">
              {body || "—"}
            </Text>
          )}
        </div>

        {/* ── Отдел и приоритет ── */}
        <Group grow align="start">
          {canEdit ? (
            <Select
              label="Отдел"
              size="sm"
              data={DEPARTMENT_OPTIONS}
              value={department}
              allowDeselect={false}
              onChange={(value) => value && setDepartment(value)}
            />
          ) : (
            <div>
              <Text size="xs" c="dimmed" fw={600}>
                ОТДЕЛ
              </Text>
              <Text size="sm">{departmentLabel}</Text>
            </div>
          )}
          {canEdit && !isCriticalPriority ? (
            <Select
              label="Приоритет"
              size="sm"
              data={PRIORITY_OPTIONS}
              value={priority}
              allowDeselect={false}
              onChange={(value) => value && setPriority(value)}
            />
          ) : (
            <div>
              <Text size="xs" c="dimmed" fw={600}>
                ПРИОРИТЕТ
              </Text>
              <Text size="sm">{getTicketPriorityLabel(ticket)}</Text>
            </div>
          )}
        </Group>

        {/* ── Контактные данные ── */}
        <div>
          <Text size="xs" c="dimmed" fw={600} mb={2}>
            КОНТАКТНЫЕ ДАННЫЕ
          </Text>
          {canEdit ? (
            <Stack gap={6}>
              {/* Имя и email из профиля — показываем как инфо, не просим вводить */}
              <Group gap={6} align="center">
                <Text size="xs" c="dimmed" style={{ minWidth: 0 }}>
                  {requesterName || "—"}
                </Text>
                {requesterEmail && (
                  <Text size="xs" c="dimmed">
                    · {requesterEmail}
                  </Text>
                )}
              </Group>
              <Group grow align="start">
                <TextInput
                  ref={officeRef}
                  size="xs"
                  placeholder="Офис *"
                  value={office}
                  maxLength={100}
                  className={!office.trim() ? "draft-field-required" : undefined}
                  onChange={(event) => setOffice(event.currentTarget.value)}
                />
                <TextInput
                  ref={affectedItemRef}
                  size="xs"
                  placeholder="Что затронуто *"
                  value={affectedItem}
                  maxLength={150}
                  className={!affectedItem.trim() ? "draft-field-required" : undefined}
                  onChange={(event) => setAffectedItem(event.currentTarget.value)}
                />
              </Group>
            </Stack>
          ) : (
            <Stack gap={2}>
              <Text size="sm">
                {requesterName || "—"}
                {requesterEmail && (
                  <Text span c="dimmed" ml={6}>
                    · {requesterEmail}
                  </Text>
                )}
              </Text>
              <Text size="sm" c="dimmed">
                {office || "—"} · {affectedItem || "—"}
              </Text>
            </Stack>
          )}
        </div>

        {/* ── Дополнительные поля (свёрнуты по умолчанию) ── */}
        {canEdit && (
          <div>
            <UnstyledButton onClick={() => setExtraOpen((v) => !v)}>
              <Group gap={4} align="center">
                {extraOpen ? (
                  <IconChevronDown size={14} />
                ) : (
                  <IconChevronRight size={14} />
                )}
                <Text size="xs" c="dimmed" fw={600}>
                  ДОПОЛНИТЕЛЬНО (необязательно)
                </Text>
              </Group>
            </UnstyledButton>
            <Collapse in={extraOpen}>
              <Stack gap={6} mt={6}>
                <Group grow align="start">
                  <TextInput
                    size="xs"
                    label="Тип запроса"
                    value={requestType}
                    maxLength={60}
                    onChange={(event) => setRequestType(event.currentTarget.value)}
                  />
                  <TextInput
                    size="xs"
                    label="Уточнение формы"
                    value={requestDetails}
                    maxLength={2000}
                    onChange={(event) => setRequestDetails(event.currentTarget.value)}
                  />
                </Group>
                <Textarea
                  size="xs"
                  label="Что уже пробовали"
                  value={stepsTried}
                  autosize
                  minRows={2}
                  maxRows={5}
                  placeholder="Например: перезагружал ноутбук, проверял кабель"
                  onChange={(event) => setStepsTried(event.currentTarget.value)}
                />
              </Stack>
            </Collapse>
          </div>
        )}

        {/* ── Предупреждение о потенциальных дубликатах ── */}
        {canEdit && potentialDuplicates && potentialDuplicates.length > 0 && (
          <Alert
            color="yellow"
            variant="light"
            icon={<IconAlertTriangle size={16} />}
            title={
              potentialDuplicates.length === 1
                ? "Похожий запрос уже открыт"
                : `Похожих запросов открыто: ${potentialDuplicates.length}`
            }
          >
            <Stack gap={4}>
              {potentialDuplicates.slice(0, 3).map((dup) => (
                <Text key={dup.id} size="xs">
                  #{dup.id} «{dup.title}» — {getStatusLabel(dup.status)}
                </Text>
              ))}
              {potentialDuplicates.length > 3 && (
                <Text size="xs" c="dimmed">
                  и ещё {potentialDuplicates.length - 3}…
                </Text>
              )}
            </Stack>
            <Text size="xs" c="dimmed" mt={6}>
              Можно отменить черновик или всё равно отправить.
            </Text>
          </Alert>
        )}

        {/* ── Действия — основное и подсказки ── */}
        {canEdit && (
          <Stack gap={6}>
            {!hasRequiredContext && (
              <Text size="xs" c="orange" ta="center">
                Заполните: {missingRequiredLabels.join(", ")}
              </Text>
            )}
            <Button
              fullWidth
              size="sm"
              color="teal"
              leftSection={<IconCheck size={18} />}
              loading={confirmLoading || saveLoading}
              disabled={!canSubmit}
              onClick={handleConfirmWithFlush}
              // aria-name = просто «Отправить» (для тестов), visible label с отделом.
              aria-label="Отправить"
            >
              Отправить в {departmentDisplayLabel}
            </Button>
            <Group justify="flex-end">
              <Button
                variant="subtle"
                color="red"
                size="xs"
                leftSection={<IconX size={14} />}
                loading={declineLoading}
                disabled={confirmLoading || saveLoading}
                onClick={onDecline}
              >
                Отменить
              </Button>
            </Group>
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}
