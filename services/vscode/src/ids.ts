export const PUBLISHER = "terraducktel";
export const EXTENSION_NAME = "terraducktel-vscode";
export const EXTENSION_ID = `${PUBLISHER}.${EXTENSION_NAME}`;
export const VIEW_WORKSPACES = "terraducktel.workspaces";
export const VIEW_RUNS = "terraducktel.runs";
export const PLAN_SCHEME = "tdt-plan";
export const CTX_SIGNED_IN = "terraducktel.signedIn";
export const CTX_CAN_WRITE = "terraducktel.canWrite";
export const CTX_FILE_MAPPED = "terraducktel.currentFileMapped";
export const CTX_HAS_PROFILES = "terraducktel.hasProfiles";
export const STATUS_BAR_CURRENT_FILE = "terraducktel.currentFile";
export const STATUS_BAR_PROFILE = "terraducktel.profileStatus";
export const CMD_CURRENT_FILE_ACTIONS = "terraducktel.currentFileActions";
/** `context.globalState` key holding the active profile name (0.3.1+). The `terraducktel.activeProfile`
 *  setting is deprecated and read only once, to migrate a pre-0.3.1 value into this key. */
export const GLOBALSTATE_ACTIVE_PROFILE = "terraducktel.activeProfile";
/** `context.globalState` flag: once true, the deprecated `terraducktel.activeProfile` setting is
 *  never consulted again — even if the migrated name later stops matching any profile (e.g. it
 *  was removed) — so a stale setting can't reassert itself. */
export const GLOBALSTATE_ACTIVE_PROFILE_MIGRATED = "terraducktel.activeProfile.migrated";
