import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Outlet, Route, Routes } from "react-router-dom";
import { IDENTITY_QUERY_KEY } from "../features/session";
import { ConversationsPage } from "../pages/ConversationsPage";
import { IdentityPage } from "../pages/IdentityPage";
import { LoginPage } from "../pages/LoginPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { SettingsPage } from "../pages/SettingsPage";
import { ApiError } from "../shared/api/client";
import { ErrorBoundary } from "./ErrorBoundary";
import { RequireSession } from "./RequireSession";
import { Shell } from "./Shell";

/**
 * Coordinamento 401 (B-03.2-26): una richiesta protetta qualunque (non solo
 * `/api/v1/me`) può scoprire per prima che la sessione è scaduta o revocata.
 * Invalidare l'identità qui, invece di lasciare che ogni feature lo scopra
 * per conto proprio, fa convergere `RequireSession` al rientro in un solo
 * punto. La query identity esclude se stessa: gestisce già il proprio 401
 * (`useIdentity`), niente invalidazione ridondante.
 */
function isSessionExpired(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

const queryClient = new QueryClient({
  defaultOptions: {
    // Gli errori sono recuperabili esplicitamente (NewRay.md §19.4):
    // nessun retry cieco dei fallimenti di dominio.
    queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (isSessionExpired(error) && query.queryKey[0] !== IDENTITY_QUERY_KEY[0]) {
        void queryClient.invalidateQueries({ queryKey: IDENTITY_QUERY_KEY });
      }
    },
  }),
  mutationCache: new MutationCache({
    onError: (error) => {
      if (isSessionExpired(error)) {
        void queryClient.invalidateQueries({ queryKey: IDENTITY_QUERY_KEY });
      }
    },
  }),
});

/**
 * Routing (B-08.1):
 * - `/` entry: signed-in → /conversations, bootstrapped → /login,
 *   altrimenti bootstrap.
 * - `/login`: success → /conversations.
 * - `/conversations` (+`/:id`) e `/settings`: protette da RequireSession
 *   (solo UX; il server autorizza) e dentro la Shell.
 * - `*`: 404 recuperabile con link a casa.
 */
export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<IdentityPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route
              element={
                <RequireSession>
                  <Shell>
                    <Outlet />
                  </Shell>
                </RequireSession>
              }
            >
              <Route path="/conversations" element={<ConversationsPage />} />
              <Route path="/conversations/:id" element={<ConversationsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </BrowserRouter>
      </ErrorBoundary>
    </QueryClientProvider>
  );
}
