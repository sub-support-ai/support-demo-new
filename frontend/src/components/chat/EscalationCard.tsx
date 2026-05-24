import {
  Alert,
  Button,
  Group,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import {
  IconArrowRight,
  IconClipboardList,
} from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";

import type {
  EscalationContext,
  IntakeState,
  RequestContextDefaults,
} from "../../api/types";

const OTHER_VALUE = "__other__";
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

const DEFAULT_OFFICE_OPTIONS = ["Главный офис", "Склад", "Удаленно"];
const DEFAULT_AFFECTED_ITEM_OPTIONS = [
  "Рабочее место",
  "Ноутбук",
  "Принтер/МФУ",
  "VPN",
  "1C",
  "Почта",
];

const REQUEST_TYPES = [
  {
    value: "vpn_issue",
    label: "VPN не работает",
    affectedItem: "VPN",
    detailsLabel: "Ошибка VPN и когда началось",
    detailsPlaceholder: "Например: ошибка 809, началось сегодня утром, пробовал переподключиться",
  },
  {
    value: "password_reset",
    label: "Сброс пароля",
    affectedItem: "Учетная запись",
    detailsLabel: "Система и логин",
    detailsPlaceholder: "Например: корпоративная почта, логин ivanov.i",
  },
  {
    value: "security_incident",
    label: "Инцидент ИБ / фишинг",
    affectedItem: "Учётная запись",
    detailsLabel: "Что произошло",
    detailsPlaceholder: "Например: пришло подозрительное письмо со ссылкой, перешёл по ссылке",
  },
  {
    value: "hardware_broken",
    label: "Сломано оборудование",
    affectedItem: "Рабочее место",
    detailsLabel: "Устройство и инвентарный номер",
    detailsPlaceholder: "Например: ноутбук HP, инв. 1042, не включается",
  },
  {
    value: "hr_request",
    label: "HR-запрос",
    affectedItem: "Кадровый документ",
    detailsLabel: "Что нужно от HR",
    detailsPlaceholder: "Например: справка о доходах за 2025 год",
  },
  {
    value: "finance_request",
    label: "Финансовый запрос",
    affectedItem: "Оплата/документы",
    detailsLabel: "Что нужно от финансов",
    detailsPlaceholder: "Например: согласовать счет, номер договора, сумма",
  },
  {
    value: "other",
    label: "Другое",
    affectedItem: "",
    detailsLabel: "Уточнение",
    detailsPlaceholder: "Опишите, какой тип запроса нужно передать специалисту",
  },
];

function toSelectOptions(values: string[], otherLabel: string) {
  const uniqueValues = Array.from(new Set(values.filter(Boolean)));
  return [
    ...uniqueValues.map((value) => ({ value, label: value })),
    { value: OTHER_VALUE, label: otherLabel },
  ];
}

function displayAsset(asset: RequestContextDefaults["assets"][number]) {
  return asset.serial_number ? `${asset.name} (${asset.serial_number})` : asset.name;
}

export function EscalationCard({
  contextDefaults,
  intakeState,
  disabled,
  loading,
  onEscalate,
}: {
  contextDefaults?: RequestContextDefaults | null;
  intakeState?: IntakeState | null;
  disabled?: boolean;
  loading?: boolean;
  onEscalate: (context: EscalationContext) => void;
}) {
  const [requesterName, setRequesterName] = useState("");
  const [requesterEmail, setRequesterEmail] = useState("");
  const [office, setOffice] = useState<string | null>(null);
  const [customOffice, setCustomOffice] = useState("");
  const [affectedItem, setAffectedItem] = useState<string | null>(null);
  const [customAffectedItem, setCustomAffectedItem] = useState("");
  const [requestType, setRequestType] = useState<string | null>(null);
  const [requestDetails, setRequestDetails] = useState("");

  const selectedRequestType = useMemo(() => {
    return REQUEST_TYPES.find((item) => item.value === requestType) ?? null;
  }, [requestType]);
  const intakeFields = useMemo(() => intakeState?.fields ?? {}, [intakeState?.fields]);
  // Инцидент ИБ/фишинг — особый случай: «затронут» не ноутбук пользователя,
  // а его учётная запись. Не подставляем основной актив, иначе тип запроса
  // и объект расходятся (фишинг ↔ ThinkPad), что выглядит как ошибка AI.
  const isSecurityContext = useMemo(
    () => (intakeState?.department ?? "").toLowerCase() === "security",
    [intakeState?.department],
  );
  // Фрагмент исходного сообщения, на основе которого AI собрал черновик —
  // снимает «магию», откуда взялись значения полей, повышает доверие.
  const sourceQuote = useMemo(() => {
    const raw = String(intakeFields.problem ?? "").trim();
    if (!raw) return "";
    return raw.length > 90 ? `${raw.slice(0, 90)}…` : raw;
  }, [intakeFields.problem]);

  useEffect(() => {
    if (!contextDefaults) {
      return;
    }
    setRequesterName((current) => current || contextDefaults.requester_name);
    setRequesterEmail((current) => current || contextDefaults.requester_email);
    setOffice((current) => current || contextDefaults.office || null);
    setAffectedItem((current) => {
      if (current) return current;
      if (isSecurityContext) return "Учётная запись";
      return contextDefaults.primary_asset
        ? displayAsset(contextDefaults.primary_asset)
        : null;
    });
  }, [contextDefaults, isSecurityContext]);

  useEffect(() => {
    if (selectedRequestType?.affectedItem) {
      setAffectedItem((current) => current || selectedRequestType.affectedItem);
    }
  }, [selectedRequestType]);

  useEffect(() => {
    setOffice((current) => current || intakeFields.office || null);
    setAffectedItem((current) => current || intakeFields.affected_item || null);
    const inferredType = REQUEST_TYPES.find((item) => {
      const requestTypeText = intakeState?.request_type?.toLowerCase() ?? "";
      return (
        requestTypeText &&
        (item.label.toLowerCase().includes(requestTypeText) ||
          item.affectedItem.toLowerCase().includes(requestTypeText))
      );
    });
    // Security-контекст всегда классифицируем как инцидент ИБ — приоритетнее
    // эвристики по тексту, чтобы фишинг не уехал в «Сброс пароля».
    const resolvedType = isSecurityContext ? "security_incident" : inferredType?.value;
    setRequestType((current) => current || resolvedType || null);

    const details = [
      intakeFields.problem,
      intakeFields.symptoms,
      intakeFields.what_tried,
      intakeFields.business_impact,
    ]
      .filter(Boolean)
      .join("\n");
    setRequestDetails((current) => current || details);
  }, [intakeFields, intakeState?.request_type, isSecurityContext]);

  const officeOptions = useMemo(() => {
    const values = [
      ...(contextDefaults?.office_options ?? DEFAULT_OFFICE_OPTIONS),
      contextDefaults?.office ?? "",
      intakeFields.office ?? "",
    ];
    return toSelectOptions(values, "Другой офис");
  }, [contextDefaults, intakeFields.office]);

  const affectedItemOptions = useMemo(() => {
    return toSelectOptions(
      [
        ...(contextDefaults?.affected_item_options ?? DEFAULT_AFFECTED_ITEM_OPTIONS),
        ...REQUEST_TYPES.map((item) => item.affectedItem),
        intakeFields.affected_item ?? "",
      ],
      "Другое",
    );
  }, [contextDefaults, intakeFields.affected_item]);

  const context = useMemo<EscalationContext>(() => {
    const resolvedOffice =
      office === OTHER_VALUE ? customOffice.trim() : office?.trim();
    const resolvedAffectedItem =
      affectedItem === OTHER_VALUE
        ? customAffectedItem.trim()
        : affectedItem?.trim();
    const selectedAsset = contextDefaults?.assets?.find((asset) => {
      return (
        displayAsset(asset) === resolvedAffectedItem ||
        asset.name === resolvedAffectedItem ||
        asset.serial_number === resolvedAffectedItem
      );
    });
    return {
      requester_name: requesterName.trim(),
      requester_email: requesterEmail.trim(),
      office: resolvedOffice || "",
      affected_item: resolvedAffectedItem || "",
      asset_id: selectedAsset?.id ?? null,
      request_type: selectedRequestType?.label ?? null,
      request_details: requestDetails.trim() || null,
    };
  }, [
    affectedItem,
    contextDefaults?.assets,
    customAffectedItem,
    customOffice,
    office,
    requesterEmail,
    requesterName,
    requestDetails,
    selectedRequestType,
  ]);

  const requesterEmailValue = context.requester_email ?? "";
  const hasPrefilledName = Boolean(contextDefaults?.requester_name);
  const hasPrefilledEmail = Boolean(contextDefaults?.requester_email);
  const canSubmit = Boolean(
    context.requester_name &&
      requesterEmailValue &&
      EMAIL_RE.test(requesterEmailValue),
  );

  return (
    <Alert
      color="gray"
      variant="light"
      icon={<IconClipboardList size={18} />}
      classNames={{
        root: "escalation-card",
        wrapper: "escalation-card-wrapper",
        body: "escalation-card-body",
        message: "escalation-card-message",
      }}
    >
      <Stack gap={6} className="escalation-stack">
        <div>
          <Text fw={600}>Черновик запроса</Text>
          <Text size="sm" c="dimmed">
            Проверьте данные — описание подтянули из диалога.
          </Text>
        </div>

        <Group grow align="start">
          <Select
            label="Тип запроса"
            data={REQUEST_TYPES.map((item) => ({
              value: item.value,
              label: item.label,
            }))}
            value={requestType}
            placeholder="Выберите сценарий"
            allowDeselect={false}
            onChange={setRequestType}
          />
          <TextInput
            label={selectedRequestType?.detailsLabel ?? "Уточнение"}
            value={requestDetails}
            maxLength={2000}
            placeholder={selectedRequestType?.detailsPlaceholder}
            onChange={(event) => setRequestDetails(event.currentTarget.value)}
          />
        </Group>

        {(!hasPrefilledName || !hasPrefilledEmail) && (
          <Group grow align="start">
            {!hasPrefilledName && (
              <TextInput
                label="Заявитель"
                value={requesterName}
                maxLength={100}
                required
                onChange={(event) => setRequesterName(event.currentTarget.value)}
              />
            )}
            {!hasPrefilledEmail && (
              <TextInput
                label="Email заявителя"
                value={requesterEmail}
                maxLength={255}
                required
                error={
                  requesterEmail && !EMAIL_RE.test(requesterEmail)
                    ? "Проверьте email"
                    : undefined
                }
                onChange={(event) => setRequesterEmail(event.currentTarget.value)}
              />
            )}
          </Group>
        )}

        <Group grow align="start">
          <Select
            label="Офис"
            data={officeOptions}
            value={office}
            placeholder="Выберите офис"
            allowDeselect={false}
            onChange={(value) => setOffice(value)}
          />
          <Select
            label="Что затронуто"
            data={affectedItemOptions}
            value={affectedItem}
            placeholder="Выберите объект"
            allowDeselect={false}
            onChange={(value) => setAffectedItem(value)}
          />
        </Group>

        {(office === OTHER_VALUE || affectedItem === OTHER_VALUE) && (
          <Group grow align="start">
            {office === OTHER_VALUE && (
              <TextInput
                label="Офис"
                value={customOffice}
                maxLength={100}
                onChange={(event) => setCustomOffice(event.currentTarget.value)}
              />
            )}
            {affectedItem === OTHER_VALUE && (
              <TextInput
                label="Что затронуто"
                value={customAffectedItem}
                maxLength={150}
                onChange={(event) =>
                  setCustomAffectedItem(event.currentTarget.value)
                }
              />
            )}
          </Group>
        )}

        {sourceQuote && (
          <Text size="xs" c="dimmed">
            AI заполнил на основе сообщения: «{sourceQuote}»
          </Text>
        )}

        <Group justify="flex-end">
          <Button
            color="teal"
            rightSection={<IconArrowRight size={16} />}
            loading={loading}
            disabled={disabled || !canSubmit}
            onClick={() => onEscalate(context)}
          >
            Создать черновик
          </Button>
        </Group>
      </Stack>
    </Alert>
  );
}
