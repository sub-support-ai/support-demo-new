export type UserRole = "user" | "agent" | "admin";

export type TicketKind =
  | "incident"
  | "service_request"
  | "access_request"
  | "security_incident";

export type TicketStatus =
  | "new"
  | "pending_user"
  | "confirmed"
  | "in_progress"
  | "resolved"
  | "closed"
  | "ai_processing"
  | "declined";

export type ConversationStatus =
  | "active"
  | "ai_processing"
  | "escalated"
  | "closed";

export type TicketMutableStatus =
  | "new"
  | "pending_user"
  | "confirmed"
  | "in_progress"
  | "resolved"
  | "closed"
  | "ai_processing"
  | "declined";

export type TicketQueue =
  | "active"
  | "new"
  | "in_progress"
  | "overdue"
  | "unassigned"
  | "pending_user"
  | "resolved"
  | "all";

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserMe {
  id: number;
  email: string;
  username: string;
  role: UserRole;
  is_active: boolean;
  agent_id?: number | null;
  agent_department?: string | null;
  request_context?: RequestContextDefaults | null;
}

export interface RequestContextDefaults {
  requester_name: string;
  requester_email: string;
  office?: string | null;
  office_source?: string | null;
  office_options: string[];
  affected_item_options: string[];
  primary_asset?: AssetSummary | null;
  assets: AssetSummary[];
}

export interface Conversation {
  id: number;
  user_id: number;
  status: ConversationStatus | string;
  /** Текущая стадия обработки AI-ответа (null = не обрабатывается). */
  ai_stage?: "thinking" | "searching" | "found_kb" | "generating" | string | null;
  intake_state?: IntakeState | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface IntakeState {
  mode?: "collecting_context" | "draft_ready" | string;
  department?: string | null;
  request_type?: string | null;
  priority?: string | null;
  fields?: Record<string, string | null | undefined>;
  required_fields?: string[];
  missing_fields?: string[];
  asked_fields?: string[];
  last_question_fields?: string[];
  last_question?: string | null;
}

export interface Source {
  title: string;
  url?: string | null;
  article_id?: number | null;
  chunk_id?: number | null;
  snippet?: string | null;
  retrieval?: "keyword" | "full_text" | "semantic" | string | null;
  score?: number | null;
  decision?: "answer" | "clarify" | "escalate" | string | null;
}

export interface Message {
  id: number;
  conversation_id: number;
  role: "user" | "ai";
  content: string;
  sources?: Source[] | null;
  ai_confidence?: number | null;
  ai_escalate?: boolean | null;
  requires_escalation?: boolean | null;
  user_feedback?: "helped" | "not_helped" | null;
  created_at?: string | null;
}

export interface MessageFeedbackPayload {
  feedback: "helped" | "not_helped";
}

export interface AddMessageResponse {
  user_message: Message;
  conversation_status: ConversationStatus | string;
  ai_job_id?: number | null;
  poll_hint: string;
}

export type AssetType =
  | "laptop"
  | "desktop"
  | "monitor"
  | "printer"
  | "phone"
  | "network_device"
  | "server"
  | "peripheral"
  | "software"
  | "service"
  | "other";

export type AssetStatus = "active" | "in_repair" | "decommissioned" | "lost";

/** Краткое представление актива, вложенное в TicketRead. */
export interface AssetSummary {
  id: number;
  asset_type: AssetType | string;
  name: string;
  serial_number?: string | null;
  status: AssetStatus | string;
  office?: string | null;
}

/** Полное представление актива — ответ GET /assets и POST /assets. */
export interface Asset extends AssetSummary {
  owner_user_id?: number | null;
  notes?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface AssetCreate {
  asset_type: AssetType | string;
  name: string;
  serial_number?: string | null;
  owner_user_id?: number | null;
  office?: string | null;
  status?: AssetStatus | string;
  notes?: string | null;
}

export interface AssetUpdate {
  asset_type?: AssetType | string;
  name?: string;
  serial_number?: string | null;
  owner_user_id?: number | null;
  office?: string | null;
  status?: AssetStatus | string;
  notes?: string | null;
}

export interface Ticket {
  id: number;
  user_id: number;
  agent_id?: number | null;
  conversation_id?: number | null;
  /** CMDB: ID актива из таблицы assets. Дополняет affected_item, не заменяет его. */
  asset_id?: number | null;
  /** Краткая информация об активе (подгружается сервером). */
  asset?: AssetSummary | null;
  title: string;
  body: string;
  user_priority: number;
  status: TicketStatus | string;
  department: string;
  ticket_source: string;
  requester_name?: string | null;
  requester_email?: string | null;
  office?: string | null;
  affected_item?: string | null;
  request_type?: string | null;
  request_details?: string | null;
  steps_tried?: string | null;
  confirmed_by_user: boolean;
  sla_started_at?: string | null;
  sla_deadline_at?: string | null;
  sla_escalated_at?: string | null;
  sla_escalation_count?: number;
  is_sla_breached?: boolean;
  reopen_count?: number;
  ticket_kind?: TicketKind | string;
  ai_category?: string | null;
  ai_priority?: string | null;
  ai_confidence?: number | null;
  ai_processed_at?: string | null;
  created_at: string;
  updated_at?: string | null;
  resolved_at?: string | null;
}

export interface TicketDraftUpdate {
  title?: string;
  body?: string;
  department?: "IT" | "HR" | "finance" | "procurement" | "security" | "facilities" | "documents";
  ai_priority?: "низкий" | "средний" | "высокий";
  ticket_kind?: TicketKind | string;
  requester_name?: string | null;
  requester_email?: string | null;
  steps_tried?: string | null;
  office?: string | null;
  affected_item?: string | null;
  asset_id?: number | null;
  request_type?: string | null;
  request_details?: string | null;
}

export interface TicketStatusUpdate {
  status: TicketMutableStatus;
}

export interface TicketReroutePayload {
  department: "IT" | "HR" | "finance" | "procurement" | "security" | "facilities" | "documents";
  reason: string;
}

// ── Bulk-операции ──────────────────────────────────────────────────────────

/** Действие массового обновления. На бэке только эти переходы поддерживаются. */
export type TicketBulkAction = "in_progress" | "resolved" | "closed";

/** Машинные коды отказа bulk-операции — для группировки в UI. */
export type TicketBulkRejectionCode =
  | "has_reopens"
  | "has_unread_user_msg"
  | "wrong_status"
  | "not_found"
  | "invalid_transition";

export interface TicketBulkRequest {
  ticket_ids: number[];
  action: TicketBulkAction;
  /** Только admin: обходит защиту от риска. Agent передавать не должен — backend 403. */
  force?: boolean;
}

export interface TicketBulkRejection {
  ticket_id: number;
  code: TicketBulkRejectionCode | string;
  reason: string;
}

export interface TicketBulkResponse {
  requested_count: number;
  applied_count: number;
  applied_ticket_ids: number[];
  rejected: TicketBulkRejection[];
}

export interface ResolveTicketPayload {
  agent_accepted_ai_response: boolean;
  routing_was_correct?: boolean;
  correction_lag_seconds?: number | null;
}

export interface EscalationContext {
  requester_name?: string | null;
  requester_email?: string | null;
  office?: string | null;
  affected_item?: string | null;
  asset_id?: number | null;
  request_type?: string | null;
  request_details?: string | null;
}

export interface EscalateResponse {
  ticket: Ticket;
  conversation_id: number;
}

export interface ApiErrorPayload {
  detail?:
    | string
    | { message?: string; fields?: string[] }
    | Array<{ loc?: Array<string | number>; msg?: string; type?: string }>;
}

export interface AppNotification {
  id: number;
  user_id: number;
  event_type: string;
  title: string;
  body: string;
  target_type?: string | null;
  target_id?: number | null;
  is_read: boolean;
  created_at: string;
  read_at?: string | null;
}

export interface NotificationUnreadCount {
  unread_count: number;
}

export interface TicketComment {
  id: number;
  ticket_id: number;
  author_id: number;
  author_username: string;
  author_role: string;
  content: string;
  internal: boolean;
  created_at: string;
}

export interface TicketCommentCreate {
  content: string;
  internal?: boolean;
}

export interface TicketFeedbackPayload {
  feedback: "helped" | "not_helped";
  reopen?: boolean;
}

export interface KnowledgeFeedbackPayload {
  message_id: number;
  article_id: number;
  feedback: "helped" | "not_helped" | "not_relevant";
}

export interface KnowledgeArticle {
  id: number;
  department?: "IT" | "HR" | "finance" | "procurement" | "security" | "facilities" | "documents" | null;
  request_type?: string | null;
  title: string;
  body: string;
  problem?: string | null;
  symptoms?: string[] | null;
  applies_to?: Record<string, string[]> | null;
  steps?: string[] | null;
  when_to_escalate?: string | null;
  required_context?: string[] | null;
  keywords?: string | null;
  source_url?: string | null;
  owner?: string | null;
  access_scope: "public" | "internal";
  version: number;
  reviewed_at?: string | null;
  expires_at?: string | null;
  is_active: boolean;
  view_count: number;
  helped_count: number;
  not_helped_count: number;
  not_relevant_count: number;
  quality_grade: "good" | "risky" | "bad" | "suppressed";
  weighted_feedback_score: number;
  quality_grade_updated_at?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface KnowledgeArticlePayload {
  department?: "IT" | "HR" | "finance" | "procurement" | "security" | "facilities" | "documents" | null;
  request_type?: string | null;
  title: string;
  body: string;
  problem?: string | null;
  symptoms?: string[] | null;
  steps?: string[] | null;
  when_to_escalate?: string | null;
  required_context?: string[] | null;
  keywords?: string | null;
  source_url?: string | null;
  owner?: string | null;
  access_scope?: "public" | "internal";
  is_active?: boolean;
}

export interface KnowledgeEmbeddingJob {
  id: number;
  article_id?: number | null;
  requested_by_user_id?: number | null;
  status: string;
  attempts: number;
  max_attempts: number;
  updated_chunks: number;
  embedding_model?: string | null;
  error?: string | null;
  run_after?: string | null;
  locked_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at?: string | null;
  is_stale: boolean;
}

export interface AIJob {
  id: number;
  conversation_id: number;
  status: string;
  attempts: number;
  max_attempts: number;
  error?: string | null;
  run_after: string;
  locked_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at?: string | null;
  is_stale: boolean;
}

export interface FailedJobsResponse {
  ai: AIJob[];
  knowledge_embeddings: KnowledgeEmbeddingJob[];
}

export type JobKind = "all" | "ai" | "knowledge_embeddings";
export type JobStatusFilter = "all" | "queued" | "running" | "done" | "failed";

export interface JobsResponse {
  ai: AIJob[];
  knowledge_embeddings: KnowledgeEmbeddingJob[];
}

export interface ResponseTemplate {
  id: number;
  department?: "IT" | "HR" | "finance" | "procurement" | "security" | "facilities" | "documents" | null;
  request_type?: string | null;
  title: string;
  body: string;
  is_active: boolean;
  created_at: string;
  updated_at?: string | null;
}

export interface TicketStats {
  total: number;
  by_status: Record<string, number>;
  by_department: Record<string, number>;
  by_source: Record<string, number>;
  /** Топ-темы обращений (ai_category → количество). */
  by_category: Record<string, number>;
  sla_overdue_count: number;
  sla_escalated_count: number;
  reopen_count: number;
  /** Среднее время первого ответа агента (секунды). null если данных нет. */
  avg_ttfr_seconds: number | null;
  /** Среднее время полного решения тикета (секунды). null если данных нет. */
  avg_ttr_seconds: number | null;
  avg_csat_score: number | null;
}

export interface AIStats {
  total_processed: number;
  avg_confidence: number;
  low_confidence_count: number;
  routing_correct_count: number;
  routing_incorrect_count: number;
  routing_accuracy_pct: number;
  resolved_by_ai_count: number;
  escalated_count: number;
  user_feedback_helped: number;
  user_feedback_not_helped: number;
}

export interface StatsResponse {
  tickets: TicketStats;
  ai: AIStats;
  jobs: JobsStats;
}

export interface JobQueueStats {
  total: number;
  queued: number;
  running: number;
  done: number;
  failed: number;
}

export interface JobsStats {
  ai: JobQueueStats;
  knowledge_embeddings: JobQueueStats;
}

export interface TrendPoint {
  /** ISO-дата YYYY-MM-DD. */
  date: string;
  count: number;
}

export interface TrendsResponse {
  period_days: number;
  /** ISO-дата YYYY-MM-DD — начало окна. */
  from_date: string;
  /** ISO-дата YYYY-MM-DD — конец окна (сегодня). */
  to_date: string;
  /** Тикеты, созданные в эту дату. Заполнено для каждого дня периода. */
  tickets_created: TrendPoint[];
  /** Тикеты, решённые в эту дату. Заполнено для каждого дня периода. */
  tickets_resolved: TrendPoint[];
}

export interface AIFallbacksStats {
  // ISO8601 — окно начинается отсюда, эхом от запроса
  since: string;
  total: number;
  // Свёртки одинаковых событий в двух разрезах:
  //   by_reason  — timeout / connect / http_5xx / broken_json / empty_response
  //   by_service — answer (chat) / classify (intake)
  by_reason: Record<string, number>;
  by_service: Record<string, number>;
}

export interface KBArticleQualityItem {
  id: number;
  title: string;
  department: string | null;
  view_count: number;
  helped_count: number;
  not_helped_count: number;
  not_relevant_count: number;
  expires_at: string | null;
  helpfulness_ratio: number | null;
}

export interface UnansweredQuery {
  query: string;
  count: number;
  last_seen: string;
}

export interface KBQualityStats {
  not_helping: KBArticleQualityItem[];
  never_shown: KBArticleQualityItem[];
  expiring_soon: KBArticleQualityItem[];
  unanswered_queries: UnansweredQuery[];
}

export interface SimilarTicket {
  id: number;
  title: string;
  department: string;
  ai_category: string | null;
  resolved_at: string | null;
}

export interface TicketAiAssist {
  summary: string | null;
  ai_response_draft: string | null;
  similar_tickets: SimilarTicket[];
}
