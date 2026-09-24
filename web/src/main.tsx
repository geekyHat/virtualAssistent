import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/app/App";
import { I18nProvider } from "@/shared/i18n";
import "@/shared/styles/global.css";

const container = document.getElementById("root");
if (!container) throw new Error("contenitore #root assente");

createRoot(container).render(
  <StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </StrictMode>
);
