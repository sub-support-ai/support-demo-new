import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@mantine/core/styles.css";
import "@mantine/charts/styles.css";
import "./styles.css";

import React from "react";
import ReactDOM from "react-dom/client";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AuthProvider } from "./stores/auth";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MantineProvider
      defaultColorScheme="light"
      theme={{
        fontFamily: "Inter, system-ui, sans-serif",
        primaryColor: "teal",
        defaultRadius: "sm",
        fontSizes: {
          xs: "12px",
          sm: "13px",
          md: "15px",
          lg: "17px",
          xl: "20px",
        },
        spacing: {
          xs: "6px",
          sm: "8px",
          md: "10px",
          lg: "12px",
          xl: "16px",
        },
        radius: {
          xs: "4px",
          sm: "6px",
          md: "8px",
          lg: "10px",
          xl: "12px",
        },
        headings: {
          fontFamily: "Inter, system-ui, sans-serif",
          fontWeight: "600",
          sizes: {
            h1: { fontSize: "28px", lineHeight: "1.18" },
            h2: { fontSize: "24px", lineHeight: "1.2" },
            h3: { fontSize: "20px", lineHeight: "1.25" },
            h4: { fontSize: "17px", lineHeight: "1.28" },
            h5: { fontSize: "15px", lineHeight: "1.3" },
            h6: { fontSize: "14px", lineHeight: "1.3" },
          },
        },
      }}
    >
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </React.StrictMode>,
);
