import { useEffect, useState } from "react";
import { useI18n } from "@/shared/i18n";
import { Button } from "@/shared/ui";
import { useModels, useModelReadiness } from "./useModels";
import { useProfiles, useProvisionAssistant } from "./useProfiles";
import { useSwitchProfileModel } from "./useSwitchProfileModel";
import { ChatErrorNotice } from "./ChatErrorNotice";

function QueryState({
  error,
  loading,
  retry,
}: {
  error: unknown;
  loading: boolean;
  retry: () => void;
}) {
  const { t } = useI18n();
  if (loading)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("loading")}
      </p>
    );
  if (error) return <ChatErrorNotice error={error} onRetry={retry} />;
  return null;
}

export function AssistantSettings() {
  const { t } = useI18n();
  const profiles = useProfiles();
  const provision = useProvisionAssistant();
  const models = useModels();
  const readiness = useModelReadiness();
  const switchModel = useSwitchProfileModel();
  const [targetProfileId, setTargetProfileId] = useState<string>();
  const [switchError, setSwitchError] = useState<unknown>(null);
  const profileItems = profiles.data?.items ?? [];
  const targetProfile = profileItems.find((profile) => profile.id === targetProfileId);

  useEffect(() => {
    if (!profileItems.some((profile) => profile.id === targetProfileId)) {
      setTargetProfileId(
        profileItems.find((profile) => profile.kind === "assistant")?.id ?? profileItems[0]?.id
      );
    }
  }, [profileItems, targetProfileId]);

  return (
    <div className="flex flex-col gap-5">
      <section aria-label={t("chat_readiness_title")} className="rounded-md border p-3 text-sm">
        <h3 className="font-medium">{t("chat_readiness_title")}</h3>
        <QueryState
          error={readiness.error}
          loading={readiness.isPending}
          retry={() => void readiness.refetch()}
        />
        {readiness.data && (
          <>
            {readiness.data.model_name && (
              <p className="break-all text-xs text-muted-foreground">
                {t("chat_readiness_model")}: {readiness.data.model_name}
              </p>
            )}
            <p role="status">{t(`chat_readiness_${readiness.data.state}`)}</p>
            {readiness.data.declared_capabilities.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {t("chat_readiness_declared")}: {readiness.data.declared_capabilities.join(", ")}
              </p>
            )}
            {"qualified_capabilities" in readiness.data &&
              Array.isArray(readiness.data.qualified_capabilities) && (
                <p className="text-xs text-muted-foreground">
                  {t("chat_readiness_qualified")}:{" "}
                  {readiness.data.qualified_capabilities.join(", ")}
                </p>
              )}
            {readiness.data.state === "installed_unverified" &&
              !readiness.data.declared_capabilities.includes("vision") && (
                <p className="text-xs text-muted-foreground">{t("chat_readiness_no_vision")}</p>
              )}
          </>
        )}
      </section>

      <section aria-label={t("chat_profile")} className="flex flex-col gap-2">
        <h3 className="font-medium">{t("chat_profile")}</h3>
        <QueryState
          error={profiles.error}
          loading={profiles.isPending}
          retry={() => void profiles.refetch()}
        />
        {!profiles.isPending &&
          !profiles.isError &&
          !profileItems.some((profile) => profile.kind === "assistant") && (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-muted-foreground">{t("chat_assistant_setup_hint")}</p>
              <Button
                type="button"
                disabled={provision.isPending}
                onClick={() => provision.mutate()}
              >
                {provision.isPending ? t("loading") : t("chat_assistant_setup")}
              </Button>
              {provision.isError && (
                <ChatErrorNotice
                  error={provision.error}
                  fallback={t("chat_assistant_provision_error")}
                />
              )}
            </div>
          )}
        {profileItems.length > 0 && (
          <>
            <label htmlFor="models-target-profile" className="text-sm">
              {t("chat_models_target_profile")}
            </label>
            <select
              id="models-target-profile"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={targetProfileId ?? ""}
              onChange={(event) => setTargetProfileId(event.target.value)}
            >
              {profileItems.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.display_name} ({profile.model?.name ?? t("chat_no_model")})
                </option>
              ))}
            </select>
          </>
        )}
      </section>

      <section aria-label={t("chat_models_toggle")} className="flex flex-col gap-2">
        <h3 className="font-medium">{t("chat_models_title")}</h3>
        <QueryState
          error={models.error}
          loading={models.isPending}
          retry={() => void models.refetch()}
        />
        {switchError != null && <ChatErrorNotice error={switchError} />}
        {!models.isPending && !models.isError && (models.data?.items.length ?? 0) === 0 && (
          <p className="text-sm text-muted-foreground">{t("chat_models_empty")}</p>
        )}
        {models.data && models.data.items.length > 0 && (
          <ul className="flex flex-col gap-2" role="list">
            {models.data.items.map((model) => {
              const current = targetProfile?.binding.model_name === model.name;
              return (
                <li
                  key={`${model.runtime}:${model.name}`}
                  className="flex items-center gap-2 text-sm"
                >
                  <span className="min-w-0 flex-1 truncate" title={model.name}>
                    {model.name}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {t(`chat_models_status_${model.status}`)}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="xs"
                    disabled={!targetProfile || current || switchModel.isPending}
                    onClick={() => {
                      if (!targetProfile) return;
                      setSwitchError(null);
                      switchModel.mutate(
                        {
                          profileId: targetProfile.id,
                          modelName: model.name,
                          expectedProfileVersion: targetProfile.version,
                        },
                        {
                          onError: (error) => setSwitchError(error),
                        }
                      );
                    }}
                  >
                    {current ? t("chat_models_current") : t("chat_models_use")}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
