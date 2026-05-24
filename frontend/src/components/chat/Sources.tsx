import { Anchor, Badge, Collapse, Group, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconFileText } from "@tabler/icons-react";
import { useDisclosure } from "@mantine/hooks";

import type { Source } from "../../api/types";

// Человекочитаемые ярлыки для типа поиска. Показываем пользователю, что
// статья нашлась через keyword-match (точное совпадение) или semantic
// (по смыслу) — это help'ает доверять/не доверять источнику.
const RETRIEVAL_LABELS: Record<string, { label: string; color: string }> = {
  keyword: { label: "Ключевые слова", color: "gray" },
  full_text: { label: "Полнотекстовый", color: "blue" },
  semantic: { label: "По смыслу", color: "violet" },
};

function retrievalBadge(retrieval: string | null | undefined) {
  if (!retrieval) return null;
  const meta = RETRIEVAL_LABELS[retrieval] ?? { label: retrieval, color: "gray" };
  return (
    <Badge size="xs" variant="light" color={meta.color}>
      {meta.label}
    </Badge>
  );
}

export function Sources({ sources }: { sources?: Source[] | null }) {
  const [opened, { toggle }] = useDisclosure(false);

  if (!sources?.length) {
    return null;
  }

  return (
    <div className="sources">
      <UnstyledButton onClick={toggle} className="sources-toggle">
        <Group gap={6}>
          {opened ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <Text size="xs" fw={600}>
            Источники ({sources.length})
          </Text>
        </Group>
      </UnstyledButton>
      <Collapse in={opened}>
        <div className="sources-list">
          {sources.map((source, index) => (
            <Stack key={`${source.title}-${index}`} gap={4} className="source-item">
              <Group gap={8} wrap="nowrap" align="center">
                <IconFileText size={14} />
                {typeof source.article_id === "number" && (
                  <Badge size="xs" variant="light" color="teal">
                    KB-{source.article_id}
                  </Badge>
                )}
                {source.url ? (
                  <Anchor href={source.url} target="_blank" size="xs">
                    {source.title}
                  </Anchor>
                ) : (
                  <Text size="xs">{source.title}</Text>
                )}
                {retrievalBadge(source.retrieval)}
                {typeof source.score === "number" && (
                  <Text size="xs" c="dimmed">
                    score {source.score.toFixed(1)}
                  </Text>
                )}
              </Group>
              {source.snippet && (
                <Text size="xs" c="dimmed" className="source-snippet">
                  {source.snippet}
                </Text>
              )}
            </Stack>
          ))}
        </div>
      </Collapse>
    </div>
  );
}
