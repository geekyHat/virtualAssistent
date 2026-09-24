import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { I18nProvider, MESSAGES, LOCALES, useI18n } from "./index";

function Probe() {
  const { locale, setLocale, t } = useI18n();
  return (
    <div>
      <span data-testid="locale">{locale}</span>
      <span data-testid="title">{t("login_title")}</span>
      <button type="button" onClick={() => setLocale("en")}>
        en
      </button>
      <button type="button" onClick={() => setLocale("it")}>
        it
      </button>
    </div>
  );
}

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("I18nProvider", () => {
  it("parte in italiano per default e imposta html.lang", () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    );
    expect(screen.getByTestId("locale")).toHaveTextContent("it");
    expect(screen.getByTestId("title")).toHaveTextContent("Accedi a NewRay");
    expect(document.documentElement.lang).toBe("it");
  });

  it("cambia lingua a runtime e aggiorna html.lang e le stringhe", async () => {
    const user = userEvent.setup();
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    );
    await user.click(screen.getByRole("button", { name: "en" }));
    expect(screen.getByTestId("locale")).toHaveTextContent("en");
    expect(screen.getByTestId("title")).toHaveTextContent("Sign in to NewRay");
    expect(document.documentElement.lang).toBe("en");
    await user.click(screen.getByRole("button", { name: "it" }));
    expect(screen.getByTestId("title")).toHaveTextContent("Accedi a NewRay");
    expect(document.documentElement.lang).toBe("it");
  });

  it("persiste la preferenza in localStorage e la rilegge al mount", () => {
    localStorage.setItem("newray.locale", "en");
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>
    );
    expect(screen.getByTestId("locale")).toHaveTextContent("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("ha a parità le chiavi it e en", () => {
    expect(Object.keys(MESSAGES.en).sort()).toEqual(Object.keys(MESSAGES.it).sort());
    expect(LOCALES).toEqual(["it", "en"]);
  });
});
