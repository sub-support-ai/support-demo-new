import {
  ActionIcon,
  AppShell,
  Badge,
  Button,
  Divider,
  Group,
  Indicator,
  Menu,
  NavLink,
  ScrollArea,
  Text,
  Title,
  useMantineColorScheme,
} from "@mantine/core";
import { useState } from "react";
import {
  IconBell,
  IconChartBar,
  IconChevronLeft,
  IconChevronRight,
  IconDatabaseSearch,
  IconFileText,
  IconListCheck,
  IconLogout,
  IconMessageCircle,
  IconMoon,
  IconRobot,
  IconSun,
} from "@tabler/icons-react";
import { NavLink as RouterNavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import appIcon from "../../../img/tp-icon-removebg-preview.png";
import { useMe } from "../../api/auth";
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotificationUnreadCount,
  useNotifications,
} from "../../api/notifications";
import { useTickets } from "../../api/tickets";
import { getRoleLabel } from "../../lib/ticketLabels";
import { useAuth } from "../../stores/auth";

export function ShellLayout() {
  const { token, logout } = useAuth();
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const [navbarOpened, setNavbarOpened] = useState(true);
  const [notificationsOpened, setNotificationsOpened] = useState(false);
  const navigate = useNavigate();
  const { data: me } = useMe(Boolean(token));
  const unreadNotifications = useNotificationUnreadCount(Boolean(token));
  const notifications = useNotifications(Boolean(token) && notificationsOpened);
  const markNotificationRead = useMarkNotificationRead();
  const markAllNotificationsRead = useMarkAllNotificationsRead();
  const tickets = useTickets({
    enabled: Boolean(token),
    refetchInterval: 30000,
  });
  const location = useLocation();
  const isChatPage = location.pathname.startsWith("/chat");
  const isOperator = me?.role === "admin" || me?.role === "agent";
  const requestsLabel =
    isOperator ? "Запросы" : "Мои запросы";
  const activeTickets =
    tickets.data?.filter(
      (ticket) =>
        ticket.confirmed_by_user &&
        ["confirmed", "in_progress"].includes(ticket.status),
    ) ?? [];
  const overdueCount = activeTickets.filter((ticket) => ticket.is_sla_breached).length;
  const unassignedCount = activeTickets.filter((ticket) => ticket.agent_id == null).length;
  const newCount = activeTickets.filter((ticket) => ticket.status === "confirmed").length;
  const userDraftCount =
    tickets.data?.filter(
      (ticket) => ticket.status === "pending_user" && !ticket.confirmed_by_user,
    ).length ?? 0;
  const requestAlertCount = isOperator
    ? overdueCount || unassignedCount || newCount
    : userDraftCount;
  const requestAlertColor =
    overdueCount > 0 ? "red" : unassignedCount > 0 ? "orange" : "blue";
  const unreadNotificationCount = unreadNotifications.data?.unread_count ?? 0;
  const isDark = colorScheme === "dark";

  return (
    <AppShell
      className={isChatPage ? "app-shell chat-shell" : "app-shell"}
      transitionDuration={250}
      transitionTimingFunction="ease"
      header={{ height: 58 }}
      navbar={{ width: navbarOpened ? 240 : 48, breakpoint: 0 }}
    >
      <AppShell.Header className="app-header">
        <Group justify="space-between" h="100%" px="md">
          <Group gap="sm">
            <img
              src={appIcon}
              alt=""
              width={30}
              height={30}
              style={{ display: "block", objectFit: "contain" }}
            />
            <Title order={3}>Точка поддержки</Title>
            {me?.role && <Badge variant="light">{getRoleLabel(me.role)}</Badge>}
          </Group>
          <Group gap="sm">
            <Group gap={6} align="center">
              <ActionIcon
                variant="subtle"
                color="gray"
                aria-label={isDark ? "Включить светлую тему" : "Включить тёмную тему"}
                onClick={() => setColorScheme(isDark ? "light" : "dark")}
              >
                {isDark ? (
                  <IconSun size={18} stroke={1.5} />
                ) : (
                  <IconMoon size={18} stroke={1.5} />
                )}
              </ActionIcon>
              <Menu
                width={360}
                position="bottom-end"
                shadow="md"
                opened={notificationsOpened}
                onChange={setNotificationsOpened}
              >
                <Menu.Target>
                  <Indicator
                    disabled={unreadNotificationCount === 0}
                    label={unreadNotificationCount}
                    size={18}
                    color="red"
                    offset={4}
                  >
                    <ActionIcon variant="subtle" color="gray" aria-label="Уведомления">
                      <IconBell size={18} stroke={1.5} />
                    </ActionIcon>
                  </Indicator>
                </Menu.Target>
                <Menu.Dropdown>
                <Group justify="space-between" px="sm" py={6}>
                  <Text fw={600} size="sm">
                    Уведомления
                  </Text>
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    disabled={unreadNotificationCount === 0}
                    loading={markAllNotificationsRead.isPending}
                    onClick={() => markAllNotificationsRead.mutate()}
                  >
                    Прочитать все
                  </Button>
                </Group>
                <Divider />
                <ScrollArea h={260}>
                  {notifications.isLoading ? (
                    <Text size="sm" c="dimmed" p="sm">
                      Загрузка...
                    </Text>
                  ) : notifications.data?.length ? (
                    notifications.data.map((notification) => (
                      <Menu.Item
                        key={notification.id}
                        className={notification.is_read ? undefined : "notification-unread"}
                        onClick={() => {
                          if (!notification.is_read) {
                            markNotificationRead.mutate(notification.id);
                          }
                          if (notification.target_type === "ticket" && notification.target_id) {
                            setNotificationsOpened(false);
                            navigate(`/tickets/${notification.target_id}`);
                          }
                        }}
                      >
                        <Text size="sm" fw={notification.is_read ? 500 : 700}>
                          {notification.title}
                        </Text>
                        <Text size="xs" c="dimmed" lineClamp={2}>
                          {notification.body}
                        </Text>
                        {notification.target_type === "ticket" && notification.target_id && (
                          <Text size="xs" c="blue" mt={2}>
                            Перейти к запросу →
                          </Text>
                        )}
                      </Menu.Item>
                    ))
                  ) : (
                    <Text size="sm" c="dimmed" p="sm">
                      Новых уведомлений нет
                    </Text>
                  )}
                </ScrollArea>
                </Menu.Dropdown>
              </Menu>
              {me && (
                <Text size="sm" c="dimmed">
                  {me.username}
                </Text>
              )}
            </Group>
            <Button
              variant="subtle"
              color="gray"
              leftSection={<IconLogout size={16} />}
              onClick={logout}
            >
              Выйти
            </Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p={navbarOpened ? "sm" : 4} style={{ overflow: "hidden" }}>
        {navbarOpened ? (
          <>
            <AppShell.Section grow>
              <NavLink
                component={RouterNavLink}
                to="/dashboard"
                label="Обзор"
                leftSection={<IconChartBar size={18} />}
                active={location.pathname.startsWith("/dashboard")}
              />
              <NavLink
                component={RouterNavLink}
                to="/chat"
                label="Чат"
                leftSection={<IconMessageCircle size={18} />}
                active={location.pathname.startsWith("/chat")}
              />
              <NavLink
                component={RouterNavLink}
                to="/tickets"
                label={requestsLabel}
                leftSection={<IconFileText size={18} />}
                rightSection={
                  requestAlertCount > 0 ? (
                    <Badge size="xs" color={requestAlertColor} variant="filled">
                      {requestAlertCount}
                    </Badge>
                  ) : undefined
                }
                active={location.pathname.startsWith("/tickets")}
              />
              {me?.role === "admin" && (
                <NavLink
                  component={RouterNavLink}
                  to="/knowledge"
                  label="База знаний"
                  leftSection={<IconDatabaseSearch size={18} />}
                  active={location.pathname.startsWith("/knowledge")}
                />
              )}
              {me?.role === "admin" && (
                <NavLink
                  component={RouterNavLink}
                  to="/jobs"
                  label="Очереди"
                  leftSection={<IconListCheck size={18} />}
                  active={location.pathname.startsWith("/jobs")}
                />
              )}
              {me?.role === "admin" && (
                <NavLink
                  component={RouterNavLink}
                  to="/ai-quality"
                  label="Качество AI"
                  leftSection={<IconRobot size={18} />}
                  active={location.pathname.startsWith("/ai-quality")}
                />
              )}
            </AppShell.Section>
            <AppShell.Section>
              <Divider mb={4} />
              <NavLink
                label="Свернуть"
                leftSection={<IconChevronLeft size={18} stroke={1.5} />}
                onClick={() => setNavbarOpened(false)}
              />
            </AppShell.Section>
          </>
        ) : (
          <>
            <AppShell.Section grow />
            <AppShell.Section>
              <div style={{ display: "flex", justifyContent: "center", paddingBottom: 4 }}>
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  onClick={() => setNavbarOpened(true)}
                  aria-label="Развернуть панель"
                >
                  <IconChevronRight size={18} stroke={1.5} />
                </ActionIcon>
              </div>
            </AppShell.Section>
          </>
        )}
      </AppShell.Navbar>

      <AppShell.Main className={`app-main${isChatPage ? " chat-main" : ""}`}>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
