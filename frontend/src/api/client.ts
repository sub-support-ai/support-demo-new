import axios, { AxiosError } from "axios";

import type { ApiErrorPayload } from "./types";

function getDefaultApiBaseUrl(): string {
  if (typeof window === "undefined") {
    return "http://localhost:8000/api/v1";
  }

  return `${window.location.protocol}//${window.location.hostname}:8000/api/v1`;
}

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || getDefaultApiBaseUrl();
export const API_TIMEOUT_MS = Number(
  import.meta.env.VITE_API_TIMEOUT_MS ?? 210000,
);

const TOKEN_KEY = "tp_access_token";

export const tokenStorage = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT_MS,
});

api.interceptors.request.use((config) => {
  const token = tokenStorage.get();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorPayload>) => {
    if (error.response?.status === 401) {
      tokenStorage.clear();
      window.dispatchEvent(new Event("tp-auth-expired"));
    }
    return Promise.reject(error);
  },
);

export function getApiError(error: unknown): string {
  if (axios.isAxiosError<ApiErrorPayload>(error)) {
    if (error.code === "ECONNABORTED") {
      return "AI отвечает дольше обычного. Запрос не потерян, но локальная модель может отвечать 30-120 секунд. Проверьте, что Mistral прогрет и backend запущен с AI_SERVICE_TIMEOUT_SECONDS.";
    }
    if (!error.response) {
      const origin = typeof window !== "undefined"
        ? window.location.origin
        : "http://localhost:5173";
      return `Не удалось подключиться к API (${API_BASE_URL}). Проверьте, что RestAPI запущен и CORS_ORIGINS разрешает ${origin}.`;
    }
    const detail = error.response?.data?.detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => item.msg)
        .filter(Boolean)
        .join("; ");
    }
    if (detail && typeof detail === "object") {
      const message = detail.message ?? "Некорректные данные";
      return detail.fields?.length
        ? `${message}: ${detail.fields.join(", ")}`
        : message;
    }
    return detail ?? error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Неизвестная ошибка";
}
