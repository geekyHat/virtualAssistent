import { test as base, type Route } from "@playwright/test";

/**
 * Fixture sintetica per il profilo `ui`: intercetta le sole rotte
 * NewRay servite dallo stesso origin (via proxy Vite in dev). Nessuna
 * chiamata di rete lascia il browser; nessun backend è richiesto.
 *
 * Ogni test dichiara esplicitamente lo scenario tramite `session.set(...)`;
 * il valore corrente è letto dalle route al momento della richiesta, così
 * gli scenari possono cambiare a metà test (bootstrap → me → revoke).
 */

export type FakeIdentity = {
  organization_id: string;
  user_id: string;
  display_name: string;
  role: "owner" | "member" | "administrator";
  session_id: string;
};

export type FakeConversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type FakeMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sequence: number;
  created_at: string;
};

export type FakeProfile = {
  id: string;
  kind: "assistant" | "researcher" | "coder";
  display_name: string;
  version: string;
  binding: {
    name: string;
    runtime: string;
    model_name: string;
    parameters: Record<string, unknown>;
  };
  model: {
    name: string;
    runtime: string;
    digest: string | null;
    status: string;
    capabilities: string[];
  } | null;
  created_at: string;
  updated_at: string;
};

/**
 * Scenari del run (B-03.2-35). `sse` serve un corpo SSE arbitrario per
 * provare i guasti di stream: terminale `length`/`cancelled`/vuoto,
 * `error` dopo delta (testo parziale), EOF senza terminale.
 */
export type RunScenario =
  | { kind: "echo" }
  | { kind: "error"; status: number; message: string }
  | { kind: "sse"; body: string };

export type ChatScenario = {
  conversations: FakeConversation[];
  messages: Record<string, FakeMessage[]>;
  profiles: FakeProfile[];
  models?: {
    name: string;
    runtime: string;
    digest: string | null;
    status: "installed" | "compatible" | "qualified";
    capabilities: string[];
  }[];
  readiness?: {
    state:
      | "not_configured"
      | "unreachable"
      | "runtime_error"
      | "catalog_empty"
      | "model_missing"
      | "artifact_incompatible"
      | "installed_unverified"
      | "available";
    model_name: string | null;
    digest: string | null;
    declared_capabilities: string[];
    qualified_capabilities?: string[];
  };
  run: RunScenario;
};

export type SessionScenario = {
  /** Risposta di `GET /api/v1/me`. */
  me:
    | { kind: "ok"; identity: FakeIdentity }
    | { kind: "unauthenticated" }
    | { kind: "invalid" }
    | { kind: "server-error"; message?: string };
  /** Risposta di `POST /api/v1/session` (bootstrap). */
  bootstrap:
    | { kind: "created"; identity: FakeIdentity }
    | { kind: "conflict" }
    | { kind: "server-error"; message?: string }
    | { kind: "network-error" };
  /** Risposta di `POST /api/v1/session/revoke`. */
  revoke: { kind: "ok" } | { kind: "server-error"; message?: string } | { kind: "network-error" };
  /** Risposta di `GET /api/v1/session/status` (B-03.2-14). */
  status: { kind: "not-bootstrapped" } | { kind: "bootstrapped" };
  /** Risposta di `POST /api/v1/session/login` (B-03.2-14). */
  login:
    | { kind: "ok"; identity: FakeIdentity }
    | { kind: "invalid" }
    | { kind: "credential-not-set" }
    | { kind: "server-error"; message?: string };
  /** Scenario chat (B-09.2). */
  chat: ChatScenario;
};

export const defaultIdentity: FakeIdentity = {
  organization_id: "org_test",
  user_id: "usr_owner",
  display_name: "Owner locale",
  role: "owner",
  session_id: "sess_test",
};

export const defaultProfile: FakeProfile = {
  id: "prof_echo",
  kind: "assistant",
  display_name: "Echo Assistant",
  version: "v1",
  binding: { name: "echo", runtime: "echo", model_name: "echo", parameters: {} },
  model: {
    name: "echo",
    runtime: "echo",
    digest: "sha256:echo000",
    status: "ready",
    capabilities: ["chat"],
  },
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

export const defaultChatScenario: ChatScenario = {
  conversations: [],
  messages: {},
  profiles: [defaultProfile],
  run: { kind: "echo" },
};

export const defaultScenario: SessionScenario = {
  me: { kind: "unauthenticated" },
  bootstrap: { kind: "created", identity: defaultIdentity },
  revoke: { kind: "ok" },
  status: { kind: "not-bootstrapped" },
  login: { kind: "ok", identity: defaultIdentity },
  chat: defaultChatScenario,
};

type SessionController = {
  set(patch: Partial<SessionScenario>): void;
  snapshot(): SessionScenario;
};

function errorPayload(
  code: string,
  message: string,
  retryable = false,
  correlationId = "cid_test"
) {
  return { code, message, retryable, correlation_id: correlationId };
}

async function fulfillMe(route: Route, scenario: SessionScenario) {
  const { me } = scenario;
  switch (me.kind) {
    case "ok":
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(me.identity),
      });
    case "unauthenticated":
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("UNAUTHENTICATED", "sessione non autenticata")),
      });
    case "invalid":
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("SESSION_INVALID", "sessione non valida")),
      });
    case "server-error":
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("INTERNAL", me.message ?? "errore interno", true)),
      });
  }
}

async function fulfillBootstrap(route: Route, scenario: SessionScenario) {
  const { bootstrap } = scenario;
  switch (bootstrap.kind) {
    case "created":
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        headers: { "set-cookie": "newray_session=fake; Path=/; HttpOnly; SameSite=Lax" },
        body: JSON.stringify(bootstrap.identity),
      });
    case "conflict":
      return route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("CONFLICT", "installazione già inizializzata")),
      });
    case "server-error":
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("INTERNAL", bootstrap.message ?? "errore interno", true)),
      });
    case "network-error":
      return route.abort("failed");
  }
}

async function fulfillStatus(route: Route, scenario: SessionScenario) {
  const bootstrapped = scenario.status.kind === "bootstrapped";
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ bootstrapped }),
  });
}

async function fulfillLogin(route: Route, scenario: SessionScenario) {
  const { login } = scenario;
  switch (login.kind) {
    case "ok":
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "set-cookie": "newray_session=fake; Path=/; HttpOnly; SameSite=Lax" },
        body: JSON.stringify(login.identity),
      });
    case "invalid":
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("INVALID_CREDENTIALS", "credenziali non valide")),
      });
    case "credential-not-set":
      return route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("CREDENTIAL_NOT_SET", "credenziale non impostata")),
      });
    case "server-error":
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("INTERNAL", login.message ?? "errore interno", true)),
      });
  }
}

async function fulfillRevoke(route: Route, scenario: SessionScenario) {
  const { revoke } = scenario;
  switch (revoke.kind) {
    case "ok":
      return route.fulfill({ status: 204 });
    case "server-error":
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("INTERNAL", revoke.message ?? "errore interno", true)),
      });
    case "network-error":
      return route.abort("failed");
  }
}

let nextConvSeq = 0;

async function fulfillConversations(route: Route, scenario: SessionScenario) {
  const method = route.request().method();
  const url = route.request().url();

  // Il vero backend applica l'autenticazione a ogni endpoint protetto: una
  // sessione scaduta a metà uso deve far fallire con 401 anche le chiamate
  // conversazioni, non solo `/api/v1/me` (prova B-03.2-26 del coordinamento).
  if (scenario.me.kind !== "ok") {
    return route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify(errorPayload("UNAUTHENTICATED", "sessione scaduta")),
    });
  }

  if (method === "GET" && url.match(/\/conversations\/?(\?|$)/)) {
    // Pagina fissa a 2 elementi: abbastanza per provare "Carica altre"
    // senza toccare gli scenari esistenti (al massimo 2 conversazioni).
    const pageSize = 2;
    const all = scenario.chat.conversations;
    const cursor = new URL(url).searchParams.get("cursor");
    const start = cursor ? all.findIndex((c) => c.id === cursor) + 1 : 0;
    const page = all.slice(start, start + pageSize);
    const nextCursor = start + pageSize < all.length ? (page.at(-1)?.id ?? null) : null;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: page, next_cursor: nextCursor }),
    });
  }

  if (method === "POST" && url.match(/\/conversations\/?$/)) {
    const body = JSON.parse(route.request().postData() ?? "{}");
    const now = new Date().toISOString();
    const conv: FakeConversation = {
      id: `conv_${++nextConvSeq}`,
      title: body.title ?? "Senza titolo",
      created_at: now,
      updated_at: now,
    };
    scenario.chat.conversations.push(conv);
    scenario.chat.messages[conv.id] = [];
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(conv),
    });
  }

  if (method === "PATCH") {
    const match = url.match(/\/conversations\/([^/?]+)/);
    const conv = match && scenario.chat.conversations.find((c) => c.id === match[1]);
    if (match && !conv) {
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify(errorPayload("NOT_FOUND", "conversazione non trovata")),
      });
    }
    if (conv) {
      const body = JSON.parse(route.request().postData() ?? "{}");
      if (body.expected_updated_at && body.expected_updated_at !== conv.updated_at) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify(
            errorPayload("CONFLICT", "la conversazione è cambiata dopo l'ultima lettura")
          ),
        });
      }
      conv.title = body.title ?? conv.title;
      conv.updated_at = new Date().toISOString();
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(conv),
      });
    }
  }

  if (method === "DELETE") {
    const match = url.match(/\/conversations\/([^/?]+)/);
    const conv = match && scenario.chat.conversations.find((c) => c.id === match[1]);
    if (match && conv) {
      const expected = new URL(url).searchParams.get("expected_updated_at");
      if (expected && expected !== conv.updated_at) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify(
            errorPayload("CONFLICT", "la conversazione è cambiata dopo l'ultima lettura")
          ),
        });
      }
      scenario.chat.conversations = scenario.chat.conversations.filter((c) => c.id !== match[1]);
      delete scenario.chat.messages[match[1]];
      return route.fulfill({ status: 204 });
    }
  }

  return route.continue();
}

async function fulfillMessages(route: Route, scenario: SessionScenario) {
  const match = route
    .request()
    .url()
    .match(/\/conversations\/([^/?]+)\/messages/);
  if (!match) return route.continue();
  const convId = match[1];
  const msgs = scenario.chat.messages[convId] ?? [];
  const url = new URL(route.request().url());
  const afterSequence = Number(url.searchParams.get("after_sequence") ?? 0);
  const limit = Number(url.searchParams.get("limit") ?? 200);
  const page = msgs.filter((message) => message.sequence > afterSequence).slice(0, limit);
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      items: page,
      next_sequence: page.length === limit ? page.at(-1)?.sequence : null,
    }),
  });
}

async function fulfillProfiles(route: Route, scenario: SessionScenario) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ items: scenario.chat.profiles }),
  });
}

async function fulfillDefaultProfile(route: Route, scenario: SessionScenario) {
  if (scenario.me.kind !== "ok") {
    return route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify(errorPayload("UNAUTHENTICATED", "sessione non autenticata")),
    });
  }
  let assistant = scenario.chat.profiles.find((profile) => profile.kind === "assistant");
  if (!assistant) {
    assistant = structuredClone(defaultProfile);
    scenario.chat.profiles.push(assistant);
  }
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(assistant),
  });
}

async function fulfillModels(route: Route, scenario: SessionScenario) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      items: scenario.chat.models ?? [
        {
          name: "echo",
          runtime: "echo",
          digest: "sha256:echo000",
          status: "qualified",
          capabilities: ["chat"],
        },
      ],
    }),
  });
}

async function fulfillModelReadiness(route: Route, scenario: SessionScenario) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(
      scenario.chat.readiness ?? {
        state: "installed_unverified",
        model_name: "echo",
        digest: "sha256:echo000",
        declared_capabilities: ["completion"],
      }
    ),
  });
}

async function fulfillProfileVersion(route: Route, scenario: SessionScenario) {
  const profileId = route
    .request()
    .url()
    .match(/\/profiles\/([^/]+)\/versions$/)?.[1];
  const profile = scenario.chat.profiles.find((item) => item.id === profileId);
  if (!profile)
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify(errorPayload("NOT_FOUND", "profilo non trovato")),
    });
  const body = JSON.parse(route.request().postData() ?? "{}");
  if (body.expected_profile_version !== profile.version)
    return route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify(errorPayload("CONFLICT", "versione del profilo cambiata")),
    });
  const model = (scenario.chat.models ?? []).find((item) => item.name === body.model_name);
  if (!model)
    return route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify(errorPayload("MODEL_UNAVAILABLE", "modello non disponibile")),
    });
  profile.version = `v${Number(profile.version.slice(1)) + 1}`;
  profile.binding = {
    ...profile.binding,
    model_name: model.name,
    name: `binding-${profile.version}`,
    runtime: model.runtime,
  };
  profile.model = model;
  return route.fulfill({
    status: 201,
    contentType: "application/json",
    body: JSON.stringify(profile),
  });
}

async function fulfillRun(route: Route, scenario: SessionScenario) {
  const { run } = scenario.chat;

  if (run.kind === "error") {
    return route.fulfill({
      status: run.status,
      contentType: "application/json",
      body: JSON.stringify(errorPayload("RUN_ERROR", run.message)),
    });
  }

  if (run.kind === "sse") {
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: run.body,
    });
  }

  const body = JSON.parse(route.request().postData() ?? "{}");
  const content: string = body.content ?? "";
  const convId =
    route
      .request()
      .url()
      .match(/\/conversations\/([^/?]+)\/run/)?.[1] ?? "";

  const msgs = scenario.chat.messages[convId] ?? [];
  const now = new Date().toISOString();
  msgs.push({
    id: `msg_${msgs.length + 1}`,
    role: "user",
    content,
    sequence: msgs.length + 1,
    created_at: now,
  });

  const reply = `Echo: ${content}`;
  const words = reply.split(" ");
  let sseBody = "";
  for (let i = 0; i < words.length; i++) {
    const text = i === 0 ? words[i] : ` ${words[i]}`;
    sseBody += `event: delta\ndata: ${JSON.stringify({ text })}\n\n`;
  }
  sseBody += `event: done\ndata: ${JSON.stringify({
    finish_reason: "stop",
    model: "echo",
    digest: "sha256:echo000",
    tokens_per_second: 42.5,
    prompt_tokens: 10,
    completion_tokens: 20,
  })}\n\n`;

  msgs.push({
    id: `msg_${msgs.length + 1}`,
    role: "assistant",
    content: reply,
    sequence: msgs.length + 1,
    created_at: now,
  });
  scenario.chat.messages[convId] = msgs;

  return route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: sseBody,
  });
}

export const test = base.extend<{ session: SessionController }>({
  session: async ({ context }, use) => {
    let current: SessionScenario = structuredClone(defaultScenario);
    await context.route("**/api/v1/me", (route) => fulfillMe(route, current));
    await context.route("**/api/v1/session", (route) => {
      if (route.request().method() === "POST") return fulfillBootstrap(route, current);
      return route.continue();
    });
    await context.route("**/api/v1/session/status", (route) => fulfillStatus(route, current));
    await context.route("**/api/v1/session/login", (route) => fulfillLogin(route, current));
    await context.route("**/api/v1/session/revoke", (route) => fulfillRevoke(route, current));
    await context.route("**/api/v1/profiles", (route) => fulfillProfiles(route, current));
    await context.route("**/api/v1/profiles/defaults", (route) =>
      fulfillDefaultProfile(route, current)
    );
    await context.route("**/api/v1/models", (route) => fulfillModels(route, current));
    await context.route("**/api/v1/models/readiness", (route) =>
      fulfillModelReadiness(route, current)
    );
    await context.route("**/api/v1/profiles/*/versions", (route) =>
      fulfillProfileVersion(route, current)
    );
    await context.route("**/api/v1/conversations/*/run", (route) => fulfillRun(route, current));
    await context.route("**/api/v1/conversations/*/messages*", (route) =>
      fulfillMessages(route, current)
    );
    await context.route(/\/api\/v1\/conversations(\/[^/]+)?(\?.*)?$/, (route) =>
      fulfillConversations(route, current)
    );
    const controller: SessionController = {
      set(patch) {
        current = { ...current, ...patch };
      },
      snapshot() {
        return current;
      },
    };
    await use(controller);
  },
});

export { expect } from "@playwright/test";
