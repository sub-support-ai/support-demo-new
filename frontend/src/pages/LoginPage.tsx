import {
  Alert,
  Button,
  Container,
  Group,
  Paper,
  PasswordInput,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { IconLock } from "@tabler/icons-react";

import appIcon from "../../img/tp-icon-removebg-preview.png";
import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { getApiError } from "../api/client";
import { useLogin, useRegister } from "../api/auth";
import { useAuth } from "../stores/auth";
import {
  AuthValidationErrors,
  PASSWORD_MAX_LENGTH,
  USERNAME_MAX_LENGTH,
  hasValidationErrors,
  validateAuthForm,
  validateEmail,
  validatePassword,
  validateUsername,
} from "../lib/validation";

export function LoginPage() {
  const navigate = useNavigate();
  const { token, setToken } = useAuth();
  const login = useLogin();
  const register = useRegister();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<AuthValidationErrors>({});

  useEffect(() => {
    if (token) {
      navigate("/dashboard", { replace: true });
    }
  }, [navigate, token]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const validationErrors = validateAuthForm({
      mode,
      email,
      username,
      password,
    });
    setFieldErrors(validationErrors);
    if (hasValidationErrors(validationErrors)) {
      return;
    }

    try {
      const result =
        mode === "login"
          ? await login.mutateAsync({ username: username.trim(), password })
          : await register.mutateAsync({
              email: email.trim(),
              username: username.trim(),
              password,
            });
      setToken(result.access_token);
      navigate("/dashboard", { replace: true });
    } catch (requestError) {
      const message = getApiError(requestError);
      if (mode === "register" && message.includes("Email")) {
        setFieldErrors((current) => ({
          ...current,
          email: "Этот email уже зарегистрирован",
        }));
      }
      if (mode === "register" && message.includes("Username")) {
        setFieldErrors((current) => ({
          ...current,
          username: "Этот логин уже занят",
        }));
      }
    }
  }

  const error = login.error || register.error;
  const loading = login.isPending || register.isPending;

  function resetMode(nextMode: "login" | "register") {
    setMode(nextMode);
    setFieldErrors({});
    login.reset();
    register.reset();
  }

  return (
    <Container size={420} className="login-container">
      <Paper p="xl" withBorder className="login-panel">
        <form onSubmit={submit}>
          <Stack gap="md">
            <div>
              <Group gap="sm" align="center" mb={4}>
                <img
                  src={appIcon}
                  alt=""
                  width={32}
                  height={32}
                  style={{ display: "block", objectFit: "contain" }}
                />
                <Title order={2}>Точка поддержки</Title>
              </Group>
              <Text size="sm" c="dimmed">
                Вход в контур поддержки
              </Text>
            </div>
            <SegmentedControl
              value={mode}
              onChange={(value) => resetMode(value as "login" | "register")}
              data={[
                { value: "login", label: "Вход" },
                { value: "register", label: "Регистрация" },
              ]}
            />
            {mode === "register" && (
              <TextInput
                label="Email"
                value={email}
                type="email"
                required
                maxLength={254}
                error={fieldErrors.email}
                onBlur={() =>
                  setFieldErrors((current) => ({
                    ...current,
                    email: validateEmail(email),
                  }))
                }
                onChange={(event) => {
                  setEmail(event.currentTarget.value);
                  setFieldErrors((current) => ({ ...current, email: undefined }));
                  register.reset();
                }}
              />
            )}
            <TextInput
              label="Логин"
              value={username}
              required
              maxLength={USERNAME_MAX_LENGTH}
              error={fieldErrors.username}
              onBlur={() =>
                setFieldErrors((current) => ({
                  ...current,
                  username: validateUsername(username),
                }))
              }
              onChange={(event) => {
                setUsername(event.currentTarget.value);
                setFieldErrors((current) => ({
                  ...current,
                  username: undefined,
                }));
                login.reset();
                register.reset();
              }}
            />
            <PasswordInput
              label="Пароль"
              value={password}
              required
              maxLength={PASSWORD_MAX_LENGTH}
              description={
                mode === "register"
                  ? "8-128 символов: строчная, заглавная, цифра и спецсимвол"
                  : undefined
              }
              error={fieldErrors.password}
              leftSection={<IconLock size={16} />}
              onBlur={() =>
                setFieldErrors((current) => ({
                  ...current,
                  password:
                    mode === "register"
                      ? validatePassword(password)
                      : password
                        ? undefined
                        : "Укажите пароль",
                }))
              }
              onChange={(event) => {
                setPassword(event.currentTarget.value);
                setFieldErrors((current) => ({
                  ...current,
                  password: undefined,
                }));
                login.reset();
                register.reset();
              }}
            />
            {error && (
              <Alert color="red" variant="light">
                {getApiError(error)}
              </Alert>
            )}
            <Button type="submit" loading={loading}>
              {mode === "login" ? "Войти" : "Создать аккаунт"}
            </Button>
          </Stack>
        </form>
      </Paper>
    </Container>
  );
}
