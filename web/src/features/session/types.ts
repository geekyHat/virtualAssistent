/**
 * Contratto identità — re-export dei tipi generati dai DTO Pydantic del
 * backend (NewRay.md §19.1). Il file generato sta in `shared/contracts` e
 * non si modifica a mano: si evolvono i DTO e si rigenera con
 * `scripts/generate_contracts.py`.
 */

import type { components } from "../../shared/contracts/api";

export type Role = components["schemas"]["Role"];

export type Identity = components["schemas"]["IdentityDTO"];
