import { ActionIcon, Badge, Button, Group, Paper, Text, Tooltip } from "@mantine/core";
import { IconThumbDown, IconThumbUp, IconX } from "@tabler/icons-react";
import { useState } from "react";

import { useSubmitMessageFeedback } from "../../api/conversations";
import { useSubmitKnowledgeFeedback } from "../../api/knowledge";
import type { Message } from "../../api/types";
import { Sources } from "./Sources";

const SECURITY_TERMS = [
  "фишинг",
  "подозрительное письмо",
  "вредоносная ссылка",
  "компрометация",
  "учётной записи",
  "учетной записи",
];

type DispatchMetadata = {
  category: string;
  priority: string;
  action: string;
  color: string;
};

function formatMessageTime(value?: string | null): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

function isSecurityAnswer(message: Message) {
  if (message.role !== "ai") return false;
  const content = message.content.toLocaleLowerCase("ru-RU");
  return SECURITY_TERMS.some((term) => content.includes(term));
}

function getKnowledgeActionLabel(decision?: string | null) {
  if (decision === "clarify") return "уточнить детали";
  if (decision === "escalate") return "оформить запрос";
  return "дать решение";
}

function getDispatchMetadata(message: Message): DispatchMetadata | null {
  if (message.role !== "ai") return null;

  if (isSecurityAnswer(message)) {
    return {
      category: "Безопасность",
      priority: "высокий",
      action: "проверка специалистом",
      color: "red",
    };
  }

  if (message.requires_escalation || message.ai_escalate) {
    return {
      category: "Запрос специалисту",
      priority: "средний",
      action: "собрать контекст",
      color: "orange",
    };
  }

  const source = message.sources?.find((item) => item.article_id);
  if (source) {
    return {
      category: "База знаний",
      priority: "обычный",
      action: getKnowledgeActionLabel(source.decision),
      color: source.decision === "escalate" ? "orange" : "blue",
    };
  }

  if (typeof message.ai_confidence === "number" && message.ai_confidence > 0 && message.ai_confidence < 0.6) {
    return {
      category: "Уточнение",
      priority: "средний",
      action: "задать вопрос",
      color: "yellow",
    };
  }

  return {
    category: "Самопомощь",
    priority: "обычный",
    action: "ответить в чате",
    color: "teal",
  };
}

function DispatchStrip({ message }: { message: Message }) {
  const metadata = getDispatchMetadata(message);
  if (!metadata) return null;

  return (
    <Group className="dispatch-strip" gap={6} mb={8} wrap="wrap">
      <Badge size="xs" variant="light" color={metadata.color}>
        {metadata.category}
      </Badge>
      <Text size="xs" c="dimmed">
        приоритет: {metadata.priority}
      </Text>
      <Text size="xs" c="dimmed">
        действие: {metadata.action}
      </Text>
    </Group>
  );
}

function SecurityActions({
  disabled,
  onActionPrompt,
}: {
  disabled?: boolean;
  onActionPrompt?: (text: string) => void | Promise<void>;
}) {
  if (!onActionPrompt) return null;

  return (
    <Group gap="xs" mt="xs" wrap="wrap" className="message-actions">
      <Button
        size="xs"
        variant="light"
        color="red"
        disabled={disabled}
        onClick={() =>
          onActionPrompt(
            "Хочу передать подозрительное письмо в безопасность. Помогите оформить запрос.",
          )
        }
      >
        Передать в безопасность
      </Button>
      <Button
        size="xs"
        variant="light"
        disabled={disabled}
        onClick={() =>
          onActionPrompt("Помогите срочно сменить пароль после подозрительного письма.")
        }
      >
        Сменить пароль
      </Button>
      <Button
        size="xs"
        variant="subtle"
        disabled={disabled}
        onClick={() => onActionPrompt("Срочно нужен специалист по информационной безопасности.")}
      >
        Нужен специалист
      </Button>
    </Group>
  );
}

function KnowledgeFeedbackActions({ message }: { message: Message }) {
  const submitFeedback = useSubmitKnowledgeFeedback();
  const [selected, setSelected] = useState<string | null>(null);
  const source = message.sources?.find((item) => item.article_id);

  if (!source?.article_id) {
    return null;
  }

  async function handleFeedback(feedback: "helped" | "not_helped" | "not_relevant") {
    if (!source?.article_id) {
      return;
    }
    setSelected(feedback);
    try {
      await submitFeedback.mutateAsync({
        message_id: message.id,
        article_id: source.article_id,
        feedback,
      });
    } catch {
      setSelected(null);
    }
  }

  if (selected) {
    return (
      <Group gap={6} mt="xs" align="center">
        <Text size="xs" c="dimmed">
          Спасибо за оценку
        </Text>
      </Group>
    );
  }

  return (
    <Group gap={4} mt="xs" align="center">
      <Text size="xs" c="dimmed" mr={4}>
        Помог ответ?
      </Text>
      <Tooltip label="Помогло" withArrow>
        <ActionIcon
          variant="subtle"
          color="teal"
          size="sm"
          loading={submitFeedback.isPending && selected === "helped"}
          onClick={() => handleFeedback("helped")}
          aria-label="Помогло"
        >
          <IconThumbUp size={16} stroke={1.5} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label="Не помогло" withArrow>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          loading={submitFeedback.isPending && selected === "not_helped"}
          onClick={() => handleFeedback("not_helped")}
          aria-label="Не помогло"
        >
          <IconThumbDown size={16} stroke={1.5} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label="Не относится к моему вопросу" withArrow>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          loading={submitFeedback.isPending && selected === "not_relevant"}
          onClick={() => handleFeedback("not_relevant")}
          aria-label="Не подходит"
        >
          <IconX size={16} stroke={1.5} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}

function MessageFeedbackActions({ message }: { message: Message }) {
  const submitFeedback = useSubmitMessageFeedback();
  const [selected, setSelected] = useState<"helped" | "not_helped" | null>(
    message.user_feedback ?? null,
  );

  async function handleFeedback(feedback: "helped" | "not_helped") {
    const previous = selected;
    setSelected(feedback);
    try {
      await submitFeedback.mutateAsync({
        conversationId: message.conversation_id,
        messageId: message.id,
        feedback,
      });
    } catch {
      setSelected(previous);
    }
  }

  if (selected) {
    return (
      <Group gap={6} mt="xs" align="center">
        <Text size="xs" c="dimmed">
          Спасибо за оценку
        </Text>
      </Group>
    );
  }

  return (
    <Group gap={4} mt="xs" align="center">
      <Text size="xs" c="dimmed" mr={4}>
        Помог ответ?
      </Text>
      <Tooltip label="Помогло" withArrow>
        <ActionIcon
          variant="subtle"
          color="teal"
          size="sm"
          loading={submitFeedback.isPending && selected === "helped"}
          onClick={() => handleFeedback("helped")}
          aria-label="Помогло"
        >
          <IconThumbUp size={16} stroke={1.5} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label="Не помогло" withArrow>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          loading={submitFeedback.isPending && selected === "not_helped"}
          onClick={() => handleFeedback("not_helped")}
          aria-label="Не помогло"
        >
          <IconThumbDown size={16} stroke={1.5} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}

export function MessageBubble({
  message,
  actionDisabled,
  onActionPrompt,
}: {
  message: Message;
  actionDisabled?: boolean;
  onActionPrompt?: (text: string) => void | Promise<void>;
}) {
  const isUser = message.role === "user";
  const hasKbArticle = Boolean(message.sources?.some((source) => source.article_id));
  const showSecurityActions = isSecurityAnswer(message);

  const time = formatMessageTime(message.created_at);

  return (
    <div className={`message-row ${isUser ? "user" : "ai"}`}>
      <Paper className={`message-bubble ${isUser ? "user" : "ai"}`} withBorder>
        <Group gap="xs" mb={4} align="center">
          <Text size="xs" fw={600} c="dimmed">
            {isUser ? "Вы" : "AI"}
          </Text>
          {time && (
            <Text size="xs" c="dimmed" ml="auto">
              {time}
            </Text>
          )}
        </Group>
        {!isUser && <DispatchStrip message={message} />}
        <Text size="sm" className="message-text">
          {message.content}
        </Text>
        {!isUser && <Sources sources={message.sources} />}
        {showSecurityActions && (
          <SecurityActions disabled={actionDisabled} onActionPrompt={onActionPrompt} />
        )}
        {!isUser &&
          (hasKbArticle ? (
            <KnowledgeFeedbackActions message={message} />
          ) : (
            <MessageFeedbackActions message={message} />
          ))}
      </Paper>
    </div>
  );
}
