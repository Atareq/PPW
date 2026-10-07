# PPW Rewards — Source Of Truth

This is the canonical reference for the PPW rewards project (PP Coins, reward
badges, reward idempotency, and the reward task list). When this file and any
other rewards document disagree, this file wins; when this file and the code
disagree, the code wins and this file must be corrected.

`docs/rewards/rewarding-system-report.md` is the original MVP implementation
report. It still holds the detailed badge story tables, but its statements are
only authoritative where they do not contradict this file.

Every statement below was checked against the code on branch
`feature/notifications-realtime`. Anything not re-verified is labelled as such.

## 1. Confirmed Architecture Decisions

These are product/architecture decisions. Do not change them without an
explicit product decision.

1. PP Coins belong to `UserAccount`.
   - `PointWallet.user` is a `OneToOneField` to `authentication.UserAccount`.
2. Buyer (Customer) and Seller modes share one PP Coin wallet.

   ```text
   UserAccount
      |
      +--- PointWallet          (one per UserAccount, holds the PP balance)
      |
      +--- CustomerProfile      (mode/context only, no PP balance)
      |
      +--- SellerProfile        (mode/context only, no PP balance)
   ```

3. `PointTransaction.user` is the PP Coin owner.
4. `PointTransaction.profile` is the profile/mode context that caused the
   transaction. It does not own a balance. It is nullable (`SET_NULL`).
5. PPW has no payment gateway. No cards, e-wallets, payment processors, gateway
   callbacks, or payment webhooks. Manual confirmation flows (`apps.payments`
   `PaymentRequest`) stay separate from PP Coins.
6. `PointService` (`apps/loyalty/services.py`) is the only code that mutates
   PP Coin balances. `PointService._create_transaction()` is the only place
   that writes `PointWallet.balance`.
7. Every reward must be idempotent.

## 2. Ledger Invariants

- `PointService._create_transaction()` runs in `transaction.atomic()`, locks the
  wallet row with `select_for_update()`, writes the new balance, and creates one
  immutable `PointTransaction`.
- `PointTransaction.idempotency_key` has a partial unique constraint
  (`loyalty_unique_transaction_idempotency_key`) for non-null, non-empty keys.
  This is the database-level duplicate guard.
- Replaying an existing idempotency key with an identical payload returns the
  existing transaction without moving the balance.
- Replaying an existing key with a different payload (different user, profile,
  points, reason, source, or metadata) raises a `ValidationError`.
- `PointValidator.validate_profile_belongs_to_user()` rejects a `profile` that
  belongs to a different `UserAccount`.
- Wallet balance can never go negative (`PositiveIntegerField` + check
  constraint + `_next_balance()` validation).
- Switching the active profile does not create wallets, transactions, or
  balance changes.

## 3. Account Onboarding Reward (BE-REW-02)

Status: **DONE**

### Required behavior

- A new PPW account receives **100 PP exactly once per `UserAccount`**.
- Creating or activating another mode later (e.g. a `SellerProfile` after the
  first `CustomerProfile`) must not award another 100 PP.
- The onboarding `PointTransaction` has `user` = the receiving `UserAccount` and
  `profile` = the profile context of the first valid onboarding trigger. That
  attribution is never rewritten.
- Welcome badges (`WELCOME_BUYER`, `WELCOME_SELLER`) stay profile-specific and
  are independent of the onboarding PP.
- The Lucky Wheel onboarding attempt is independent of the onboarding PP.

### Pre-change findings (current branch, before BE-REW-02)

Onboarding entry points that call the reward engine:

| Entry point | Event | Lucky Wheel hook |
| --- | --- | --- |
| `POST /api/auth/register/customer/` (`CustomerRegisterSerializer.create`) | `customer_profile_created` | `grant_onboarding_attempt(customer_profile)` |
| `POST /api/users/mobile/sellers/` (`SellerProfileMobileViewSet.create`) | `seller_profile_created` | `grant_onboarding_attempt(seller_profile)` |
| `POST /api/users/mobile/customers/` (`CustomerProfileMobileViewSet.create`) | `customer_profile_created` | `grant_onboarding_attempt(customer_profile)` |

Findings:

1. Onboarding PP was granted only through `RewardEngine._grant_reward()` for the
   `WELCOME_BUYER` and `WELCOME_SELLER` badges, each worth 100 PP in
   `REWARD_POINTS`.
2. **Bug confirmed:** customer registration (+100) followed by seller profile
   creation (+100) gave the shared wallet 200 PP.
3. Point keys were
   `reward:WELCOME_BUYER:customer_profile_created:CustomerProfile:{profile_id}:profile:{profile_id}`
   and
   `reward:WELCOME_SELLER:seller_profile_created:SellerProfile:{profile_id}:profile:{profile_id}`.
4. Idempotency was **profile-scoped**, not user-scoped.
5. Welcome badges and onboarding PP were coupled: the same `_grant_reward()`
   call created both, sharing one idempotency key.
6. The Lucky Wheel onboarding attempt is **not** coupled to the PP: it is a
   separate call with its own key `lucky_wheel:onboarding:{profile_id}`. That
   key is also profile-scoped, so customer + seller creation yields two
   attempts. This is existing Lucky Wheel behavior and is out of scope here.
7. Registration retries cannot duplicate points: each successful registration
   creates a new `UserAccount` (unique username/phone), and replaying the event
   for the same profile hit the same key.
8. Alternate flows:
   - `Profile.Meta.unique_together = ("user", "type")` allows at most one
     customer and one seller profile row per user, including soft-deleted rows.
   - Manager registration and admin profile CRUD do not call the reward engine.
   - `POST /api/users/mobile/customers/` currently fails with
     `IntegrityError: NOT NULL constraint failed: users_profile.user_id`
     because the view never passes `user` to `serializer.save()`. Its reward hook
     is therefore unreachable today. This is a pre-existing bug outside
     BE-REW-02.
   - `RewardSourceType.ONBOARDING` existed but no point transaction used it.

### Final behavior (implemented and tested)

- `PointWallet` ownership = `UserAccount`. Customer and seller modes share it.
- Onboarding reward = **100 PP** by default. Since BE-REW-03 the amount and an
  on/off switch come from the `ACCOUNT_ONBOARDING` reward rule through
  `RewardRuleService` (section 4); `ACCOUNT_ONBOARDING_POINTS` no longer exists.
- Onboarding PP is paid **once per `UserAccount`** by
  `RewardEngine.grant_account_onboarding_reward(user, profile, result=None)`:
  - idempotency key `reward:onboarding:user:{user_id}`
  - `source_type="onboarding"` (`RewardSourceType.ONBOARDING`),
    `source_id=str(user_id)`, reason `Account onboarding reward`,
    metadata `{"reward_code": "onboarding"}`
  - `user` = the receiving account; `profile` = the first triggering profile.
    Later calls return the original transaction and never rewrite `profile`.
- Both `customer_profile_created` and `seller_profile_created` call the helper,
  so every current onboarding entry point uses the same logic. Whichever mode is
  created first gets the +100 attribution; the second mode gets +0.
- The helper rejects a profile that belongs to another account
  (`PointValidator.validate_profile_belongs_to_user`).
- Concurrency: the helper runs in `transaction.atomic()`, locks the user's
  `PointWallet` row with `select_for_update()`, then checks the ledger before
  calling `PointService.earn_points()`. Concurrent onboarding calls for one
  account are serialized on that row, and the partial unique constraint on
  `PointTransaction.idempotency_key` is the database backstop.
- Legacy data: accounts onboarded before BE-REW-02 hold a point transaction
  keyed `reward:WELCOME_BUYER:...` or `reward:WELCOME_SELLER:...`. The helper
  treats those rows as proof of onboarding, so such an account creating its
  other mode later also gets +0. No data migration is needed.
- Welcome badges stay separate and profile-specific: `WELCOME_BUYER` on the
  customer profile, `WELCOME_SELLER` on the seller profile. Both are awarded via
  a badge-only path and carry no points (since BE-REW-06 via
  `RewardEngine._award_badge()`; `REWARD_POINTS` no longer exists).
- At BE-REW-02, `LuckyWheelGrantService.grant_onboarding_attempt()` was still
  called separately per created profile (`lucky_wheel:onboarding:{profile_id}`).
  Since **BE-DRW-01** (section 21), account onboarding no longer grants a Lucky
  Wheel spin attempt; instead, first-profile onboarding creates one account-level
  `WeeklyDrawEntry` (`draw:onboarding:user:{user_id}`) via
  `WeeklyDrawEntryService.ensure_for_first_profile_onboarding`.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/loyalty/reward_engine.py` | Added `grant_account_onboarding_reward()`, `account_onboarding_idempotency_key()`, `LEGACY_ONBOARDING_KEY_PREFIXES`; welcome handlers award onboarding PP via the helper and badges with `award_points=False` | One user-scoped onboarding reward; badges decoupled from points |
| `apps/loyalty/constants.py` | Added `ACCOUNT_ONBOARDING_POINTS = 100` (replaced by the `ACCOUNT_ONBOARDING` rule default in BE-REW-03); removed welcome badges from `REWARD_POINTS` | Onboarding amount lives in one place; no badge path can pay 100 PP again |
| `tests/loyalty/test_account_onboarding_reward.py` | New, 10 tests | BE-REW-02 regression coverage |
| `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md` | New | Canonical rewards document |
| `docs/rewards/rewarding-system-report.md` | Corrected welcome badge points (100 -> 0), added onboarding exception and pointer to this file | Remove contradictions |
| `AGENTS.md` | Loyalty and Lucky Wheel bullets updated | Keep the living guide aligned |

### Tests added (`tests/loyalty/test_account_onboarding_reward.py`)

1. Customer registration API -> balance 100, one onboarding transaction with the
   user-scoped key and the customer profile as context.
2. Seller mode created later through `POST /api/users/mobile/sellers/` -> balance
   stays 100, one onboarding transaction, original profile attribution kept.
3. Seller-first at reward-engine level -> +100 attributed to seller, later
   customer mode +0 (reported as `already_awarded`).
4. Helper called three times (two profiles) -> one transaction, balance 100.
5. Helper rejects a profile from another account and writes nothing.
6. Legacy `reward:WELCOME_BUYER:...` 100 PP row -> seller mode creation adds no
   points; `WELCOME_SELLER` badge still awarded.
7. Two users each get 100 with distinct keys.
8. `WELCOME_BUYER` and `WELCOME_SELLER` both awarded while only one 100 PP
   transaction exists.
9. After switching to seller mode, `/api/loyalty/points/` shows balance 100 with
   the seller as `active_profile_id`.
10. Lucky Wheel regression: exactly one onboarding grant per created profile,
    each with one remaining attempt.

The pre-change bug was reproduced before the fix: customer registration plus
seller mode creation produced 200 PP from two profile-scoped keys.

### Test results (2026-09-27, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/loyalty/test_account_onboarding_reward.py -q` | 10 passed |
| `pytest tests/loyalty tests/rewards_sprint tests/users tests/authentication -q` with `LANGUAGE_CODE="en"` | 171 passed |
| same, with the current working-tree `ppw/settings/test.py` | 9 failed / 162 passed; all 9 are English-message assertions that fail because the uncommitted working tree removes `LANGUAGE_CODE = "en"` from test settings. The same 9 fail without BE-REW-02 |
| `pytest -q` (full suite) | Failure set identical to the pre-change baseline under both settings variants (47 with current working-tree settings, 10 with `LANGUAGE_CODE="en"`: chat, payments detail permission, i18n cart message, two migration tests). None touch rewards |
| `python manage.py makemigrations --check --dry-run` | No changes detected (exit 0) |

### Migration status

No migration. The existing `PointTransaction` ledger, idempotency constraint,
and `RewardSourceType.ONBOARDING` choice were enough.

### Remaining risks

- Accounts that already received 200 PP under the old code keep it. There is no
  clawback or reversal policy.
- ~~The badge catalog still listed `criteria_value: {"points": 100}` for the
  welcome badges.~~ Resolved in BE-REW-06 (section 7).
- Lucky Wheel onboarding attempts are profile-scoped, so customer + seller
  creation grants two attempts although the service docstring says
  "first-profile". Needs a product decision; untouched here.
- `POST /api/users/mobile/customers/` fails with an `IntegrityError` (the view
  does not pass `user` to `serializer.save()`), so the seller-first path cannot
  currently be exercised through the API. Pre-existing and outside BE-REW-02.
- Tests run on SQLite, which does not enforce `select_for_update()` row locks.
  True concurrent onboarding behavior should be verified on PostgreSQL.

## 4. Configurable Reward Rule Settings (BE-REW-03)

Status: **DONE**

### Goal

Move product-tunable reward values (enabled, points, multiplier, threshold,
small structured options) into one controlled configuration layer. Business
conditions stay in Python. This is not a rule or expression engine.

### Pre-change findings

Hard-coded reward values on the branch before BE-REW-03:

| Value | Location | Classification |
| --- | --- | --- |
| `ACCOUNT_ONBOARDING_POINTS = 100` | `apps/loyalty/constants.py` | Product config -> `ACCOUNT_ONBOARDING` |
| 50 PP per auction posted | `RewardEngine._handle_auction_published` | Product config -> `AUCTION_PUBLISHED` |
| 30 PP to the rated profile owner for any rating | `RewardEngine._handle_rating_created` | Product config (current `rating_received`); not the same as `POSITIVE_RATING` |
| 200 PP first successful auction purchase after bidding (buyer) | `RewardEngine._grant_auction_completion_extras` | Product config (current `first_bid_completed`); not the same as `REFERRAL_FIRST_BID` |
| Market item completion: `int(total_price)` PP + 100 PP bonus to buyer and to seller | `RewardEngine._grant_order_completion_points` | Product config; closest existing behavior to `DEAL_COMPLETED` (market only) |
| `REWARD_POINTS` badge points (10, 5, 50, 30, 10, 5, 25, 25, 75, 50, 100, 150) | `apps/loyalty/constants.py` | Badge-coupled points; BE-REW-06 scope |
| Trusted buyer/seller thresholds (10), badge validity days (90, 7), last-minute window (5 min) | `apps/loyalty/constants.py` | Badge rule values; BE-REW-06 scope |
| `REFERRAL_INVITER_POINTS = 30` | `apps/referrals/services.py` | Product config -> `REFERRAL_APPROVED` (removed in BE-REF-01; the rule is wired) |
| Referral code TTL 24 h, new-account window 2 h | `apps/referrals/services.py` | Referral policy; stays in referrals |
| Season winner PP `{1: 100000, 2: 50000, 3: 25000}`, 10 completed items to qualify | `apps/seasons/services.py` | Seasons config; seasons out of scope |
| Cashback -> PP rate 20 per unit (written twice) | `apps/promotions/services.py` | Conversion rate, not a reward rule |
| Lucky Wheel prize PP | `LuckyWheelPrize.points_amount` | Already database-configured |

Implementation constants that stay constants: idempotency key formats,
`LEGACY_ONBOARDING_KEY_PREFIXES`, event/source type enums, and the static badge
catalog metadata.

Existing infrastructure:

- No generic settings/configuration model exists, and no settings package
  (django-constance or similar) is installed.
- The closest pattern is manager-configured tables behind exact manager
  permissions: `LuckyWheelPrize` (`/api/lucky-wheel/admin/prizes/`) and `Season`
  (`/api/seasons/`), both using `HasAppAccess` + `manager_permission_map`.
- Admin actor convention is a nullable `SET_NULL` FK. Superusers can use manager
  APIs without a `ManagerProfile`, so the actor is stored as a `UserAccount`
  (as `ProfileDataChangeLog.changed_by_user` and `PedigreeDocument.uploaded_by`
  do).

### Architecture (implemented)

```text
RewardEngine / business services
        |
        v
RewardRuleService            (apps/loyalty/services.py)
        |
        v
canonical defaults           (DEFAULT_REWARD_RULES, apps/loyalty/constants.py)
  + optional DB override     (RewardRuleConfig row, one live row per code)
```

- `RewardRuleCode` (in `apps/loyalty/constants.py`) is the closed list of
  supported rule codes. `DEFAULT_REWARD_RULES` is the only place that holds
  default values.
- `RewardRuleConfig` (`apps/loyalty/models.py`) stores an override: `code`,
  `enabled`, `points`, `multiplier`, `threshold`, `configuration`, and
  `updated_by` (FK to `UserAccount`, `SET_NULL`), plus the standard
  `created`/`modified`/soft-delete fields. Only one live row can exist per code
  (partial unique constraint on non-deleted rows).
- A row exists only after a manager changes that rule. There is no seed data
  migration, so defaults are never duplicated in the database, and the app is
  correct with an empty table.
- Resolution: override row if present, otherwise the canonical default. An
  override row is a full snapshot (it is created from the current default and
  then patched), so once a rule is overridden later changes to its code
  default do not affect it. Deleting the row (soft delete) restores the
  default.
- Unknown codes fail loudly: `RewardRuleService` raises a `ValidationError`
  instead of returning zero or `None`.
- `RewardRuleService` API: `get_rule(code)`, `get_points(code)`,
  `get_threshold(code)`, `is_enabled(code)`, `list_rules()`,
  `update_rule(code, *, updated_by, **changes)`, `is_known_code(code)`.
- Only `ACCOUNT_ONBOARDING` is read by backend code in this task
  (`is_implemented=True`). Every other rule is configuration only.
  (Since BE-REW-07, `ACTIVE_AUCTION_BIDDER` is also read; section 8.)

### Rule catalog and defaults

| Code | Default | Required fields | Read by code | Status |
| --- | --- | --- | --- | --- |
| `ACCOUNT_ONBOARDING` | 100 PP | `points` | Yes (`RewardEngine.grant_account_onboarding_reward`) | Confirmed |
| `AUCTION_PUBLISHED` | 50 PP | `points` | No (engine still pays a hard-coded 50) | Confirmed value |
| `ACTIVE_AUCTION_BIDDER` | 50 PP, threshold 5 | `points`, `threshold` | Yes since BE-REW-07 (`RewardEngine._grant_active_auction_bidder_rewards`) | Confirmed; implemented |
| `MUTUAL_RATING` | 20 PP | `points` | Yes since BE-REW-10 (`_handle_mutual_rating_completed`) | Confirmed; implemented (section 11) |
| `REFERRAL_APPROVED` | 30 PP | `points` | Yes since BE-REF-01 (paid on manager approval; `REFERRAL_INVITER_POINTS` removed) | Confirmed; implemented (section 12) |
| `REFERRAL_FIRST_BID` | 200 PP | `points` | Yes since BE-REF-02 (`_grant_referral_first_bid_reward` on `BID_PLACED`) | Confirmed; implemented (section 13) |
| `DEAL_COMPLETED` | multiplier 2, EGP/USD rate 1 (since BE-REW-08) | `multiplier` | Yes since BE-REW-08 | Confirmed (section 9) |
| `DEAL_COMPLETION_BONUS` | 100 PP (added in BE-REW-08) | `points` | Yes since BE-REW-08 | Confirmed (section 9) |
| `SUCCESSFUL_SALE` | 100 PP (added in BE-REW-09) | `points` | Yes since BE-REW-09 | Confirmed (section 10) |
| `POSITIVE_RATING` | none | none | No | **Pending business clarification** |

Pending rules have `points=None`; no value was invented
(`DEAL_COMPLETED` was resolved in BE-REW-08, section 9; the note below is the
BE-REW-03 record):

- `DEAL_COMPLETED`: the closest current behavior is market-only
  (`int(total_price)` PP + 100 PP bonus to buyer and to seller on an approved
  `MarketPaymentRequest`). No document defines one deal-completion rule across
  auction and market deals.
- `POSITIVE_RATING`: current code pays 30 PP for any rating received, whatever
  the stars. No document defines what counts as positive or its value.

At BE-REW-03 all defaults had `enabled=True`, `multiplier=None`, and
`configuration={}`. Since BE-REW-08, `DEAL_COMPLETED` uses `multiplier=2` and
per-currency rates in `configuration`.

### Onboarding integration

- `grant_account_onboarding_reward()` reads the `ACCOUNT_ONBOARDING` rule. The
  BE-REW-02 idempotency logic is unchanged: same key
  `reward:onboarding:user:{user_id}`, same wallet lock, same legacy-key proof,
  and an already-onboarded account always gets its existing transaction back.
- If the account was not yet onboarded and the rule is disabled or its points
  are 0, nothing is awarded and the helper returns `None`. Welcome badges and
  the Lucky Wheel onboarding attempt are still granted.
- Because nothing is recorded while disabled, an account that registered while
  the rule was disabled receives onboarding PP on its next onboarding trigger
  (creating its other mode) if the rule is enabled by then. Still once per
  account.
- Config changes apply only to future rewards. Existing `PointTransaction`
  rows are immutable history and are never rewritten.

### Management API (manager-only)

| Method | Path | Manager permission |
| --- | --- | --- |
| `GET` | `/api/loyalty/admin/reward-rules/` | `reward_rules.list` |
| `GET` | `/api/loyalty/admin/reward-rules/<code>/` | `reward_rules.retrieve` |
| `PATCH` | `/api/loyalty/admin/reward-rules/<code>/` | `reward_rules.update` |

- `RewardRuleAdminViewSet` uses `HasAppAccess` with
  `permission_resource = "REWARD_RULE_ADMIN"`, which is not in
  `CUSTOMER_PERMISSIONS` or `SELLER_PERMISSIONS`, so customers and sellers get
  403. Managers need the exact codes above; superusers bypass as elsewhere.
- The list returns every supported code with its effective values (paginated),
  including `label`, `required_fields`, `is_implemented`, `is_overridden`,
  `updated_by_id`, and `updated_at`.
- `PATCH` accepts `enabled`, `points`, `multiplier`, `threshold`, and
  `configuration`. The first `PATCH` of a code creates its override row and
  records `updated_by`.
- There is no create or delete route. `POST`/`DELETE` return 403 for managers
  (no manager permission maps to them) and 405 for superusers. Unknown codes
  return 404. `code`, `is_implemented`, and the audit fields cannot be changed.
- Admins can tune supported reward values. Admins cannot create reward codes or
  submit executable logic.
- `RewardRuleConfig` is not registered in Django admin, so the manager API is
  the only validated write path.

### Validation

- `points >= 0`, `threshold >= 0` (serializer `min_value=0`; DB also enforces
  non-negative through `PositiveIntegerField`).
- `multiplier >= 0` (model validator and DB check constraint), up to 10 digits
  with 4 decimal places.
- Required fields per rule cannot be cleared: `points` for every confirmed rule,
  and also `threshold` for `ACTIVE_AUCTION_BIDDER`. Pending rules accept `null`.
- `configuration` must be a JSON object with at most 20 keys whose values are
  plain values or lists of plain values. Nested objects are rejected, so it
  cannot hold rule trees or expressions. It is never executed.
- `points = 0` is allowed and means the rule pays nothing.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/loyalty/constants.py` | Added `RewardRuleCode`, `DEFAULT_REWARD_RULES`, `REWARD_RULE_TUNABLE_FIELDS`, `REWARD_RULE_CONFIGURATION_MAX_KEYS`; removed `ACCOUNT_ONBOARDING_POINTS` | Single home for supported codes and defaults |
| `apps/loyalty/models.py` | Added `RewardRuleConfig` | Optional manager overrides |
| `apps/loyalty/migrations/0003_reward_rule_config.py` | New schema migration | Create the override table and constraints |
| `apps/loyalty/services.py` | Added `RewardRule` and `RewardRuleService` | Central lookup with default fallback |
| `apps/loyalty/reward_engine.py` | Onboarding helper reads the `ACCOUNT_ONBOARDING` rule | Replace the hard-coded amount |
| `apps/loyalty/serializers.py` | Added `RewardRuleSerializer`, `RewardRuleUpdateSerializer` | API representation and validation |
| `apps/loyalty/views.py` | Added `RewardRuleAdminViewSet` | Manager list/retrieve/partial update |
| `apps/loyalty/urls.py` | Added `admin/reward-rules/` routes | Expose the API |
| `apps/users/manager_permissions.py` | Added `reward_rules.list/retrieve/update` | Exact manager permissions |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | 21 Arabic entries; `.mo` recompiled with `msgfmt` (only those 21 entries differ) | Arabic-first API text |
| `tests/loyalty/test_reward_rule_config.py` | New, 27 tests | BE-REW-03 coverage |
| `tests/loyalty/test_account_onboarding_reward.py` | Dropped the removed constant import; assertion still expects 100 | Adapt BE-REW-02 test |
| `AGENTS.md`, `docs/rewards/rewarding-system-report.md` | Aligned with configurable onboarding amount and the new API | No contradictions |

### Tests added (`tests/loyalty/test_reward_rule_config.py`)

- Default `ACCOUNT_ONBOARDING` = 100, enabled, not overridden, no DB row.
- `ACTIVE_AUCTION_BIDDER` default = 50 PP, threshold 5; bidder reward not implemented.
- Pending rules have no invented points.
- Unknown codes raise in the service and create nothing.
- Deleting an override falls back to the default; a new override can be created afterwards.
- Override 150 -> new account gets 150 exactly once (two onboarding triggers).
- Disabled rule -> no onboarding PP; `WELCOME_BUYER` badge and Lucky Wheel onboarding attempt still granted.
- 0 points -> no onboarding PP.
- Idempotency with a configured value: 150 awarded; config changed to 200; second trigger returns the same 150 transaction.
- No retroactive change: user A keeps 100 after the config becomes 150; user B gets 150.
- Manager lists all 8 codes with defaults; `PATCH` creates the override and records `updated_by`.
- Protected fields (`code`, `is_implemented`) are ignored.
- Negative `points`, `threshold`, `multiplier` -> 400, no row.
- Required fields cannot be cleared; pending rule accepts `null` points.
- `configuration` must be a flat object.
- Unknown code cannot be created: `POST` 403, `PATCH`/`GET` unknown code 404, `DELETE` 403; superuser gets 405.
- Customers and sellers get 403 on read and write.
- A manager with only list/retrieve permissions cannot `PATCH`.

### Test results (2026-09-27, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/loyalty/test_reward_rule_config.py tests/loyalty/test_account_onboarding_reward.py -q` | 37 passed |
| `pytest tests/loyalty -q` with `LANGUAGE_CODE="en"` | 102 passed |
| `pytest tests/loyalty -q` with the current working-tree settings | 7 failed / 95 passed (known Arabic-message failures) |
| `pytest tests/rewards_sprint -q` (both settings) | 8 passed |
| `pytest tests/users tests/authentication -q` | 88 passed with `en`; 2 known Arabic-message failures with current settings |
| `pytest tests/test_i18n.py tests/test_api_docs.py -q` | 13 passed with current settings; 2 known failures with `en` (they expect the Arabic default) |
| `pytest -q` (full suite) | Failure set identical to the pre-BE-REW-02 baseline under both settings (47 current / 10 `en`); passes rose from 808 to 835 |
| `python manage.py makemigrations --check --dry-run` | No changes detected (exit 0) |
| `python manage.py spectacular --validate` | New endpoints generate; the only errors are pre-existing ones in `loft_subscriptions` and `users` views |

The known failures come from the uncommitted working-tree removal of
`LANGUAGE_CODE = "en"` from `ppw/settings/test.py` plus pre-existing chat,
payments, i18n, and migration test failures. None are caused by BE-REW-03.

### Migration status

One schema migration: `apps/loyalty/migrations/0003_reward_rule_config.py`
(creates `RewardRuleConfig`, the live-code unique constraint, and the multiplier
check constraint). No data migration. Not applied to any local or production
database as part of this task.

Correction (BE-REF-01): this originally said the test database applies the
migration. It does not: `ppw/settings/test.py` sets
`MIGRATION_MODULES = DisableMigrations()`, so tests build tables from the
models. All rewards migrations (deals 0001, loyalty 0003-0005, ratings 0002,
referrals 0002-0003) were verified by running `migrate` from scratch on a
scratch SQLite database with migrations enabled (see section 12).

### Remaining risks

- Only `ACCOUNT_ONBOARDING` is live (plus `ACTIVE_AUCTION_BIDDER` since
  BE-REW-07). Managers can edit `AUCTION_PUBLISHED` and
  `REFERRAL_APPROVED` today, but those payouts still use hard-coded 50 and 30,
  so edits have no effect until the rules are wired. The API marks them
  `is_implemented=false`. (`REFERRAL_APPROVED` is wired since BE-REF-01;
  `AUCTION_PUBLISHED` is still hard-coded.)
- Until they are wired, the 50 and 30 values exist twice (rule default and the
  hard-coded payout). When wiring a rule, remove the hard-coded value and set
  `implemented=True` in `DEFAULT_REWARD_RULES`.
- `REFERRAL_FIRST_BID` (200 PP, referral) is not the existing
  `first_bid_completed` 200 PP buyer reward. Do not wire one to the other
  without a product decision.
- Disabling `ACCOUNT_ONBOARDING` defers rather than cancels onboarding PP for
  accounts registered while it is off (see Onboarding integration).
- There is no reset-to-default endpoint; restoring a default means deleting the
  override row outside the API.
- On the SQLite test database, DRF did not derive `min_value=0` from
  `PositiveIntegerField`, so negative input reached the DB check constraint
  (500) until `min_value=0` was set explicitly. Other `ModelSerializer`s that
  rely on `PositiveIntegerField` may behave differently on SQLite and
  PostgreSQL; not investigated here.
- Tests run on SQLite, which does not enforce `select_for_update()` row locks.

## 5. Canonical Completed Deal Boundary (BE-REW-04)

Status: **DONE**

### Goal

Introduce a neutral business record meaning "PPW considers this buyer/seller
transaction successfully completed", separate from the manual confirmation
workflow. It is not a payment and not proof of an electronic payment.

### Pre-change findings

1. **Auction completion today:** `AuctionItem` becomes `sold` at settlement
   (`SettlementService._settle_item`) or buy-now. That is only "won". The deal
   completes when the seller approves the winner's `AuctionPaymentRequest`
   (`PaymentRequestService.approve` -> `_approve_auction_request`), which
   transfers the bird (and `paired_bird`) ownership. `AuctionItem` has no later
   status; it stays `sold`.
2. **Market completion today:** `OrderItem` goes `pending_seller` -> `approved`
   (seller item approval, cashback redemption) -> `completed` only when the
   seller approves the `MarketPaymentRequest` (`_approve_market_request`): stock
   is reduced, birds change owner, the item becomes `completed`, cashback is
   earned, and the order status is refreshed.
3. **Where mutations happen:** stock and ownership change only inside
   `PaymentRequestService.approve()`.
4. **Reward hooks:** `_handle_approved_rewards()` runs synchronously at the end
   of `approve()`, inside the same `transaction.atomic()` block, and calls
   `RewardEngine.handle_event()` with the `PaymentRequest` as context.
5. **Idempotency:** `approve()` locks the request row and requires `pending`;
   a second approval raises "Only pending requests can be approved." and
   mutates nothing. A request cannot be approved twice. Approved requests
   cannot be updated or rejected afterwards, and complaints do not change them.
6. **One source per deal:** `MarketPaymentRequest.order_item` and
   `AuctionPaymentRequest.auction_item` are `OneToOneField`s, so each order item
   or auction item has at most one confirmation request.
7. **Semantics differ:** market completion has a neutral business state
   (`OrderItem.completed`); auction completion does not, so the approved
   `AuctionPaymentRequest` is the only completion signal for auctions.
8. **No equivalent model exists.** Completed sales are currently inferred from
   `OrderItem.status=completed` (seasons, ratings, levels, insights, feed) or
   `PaymentRequest.status=approved` (trusted buyer/seller aggregates). Nothing
   covers both deal types.
9. **Amount snapshot already exists:** `OrderItem.total_price` (market) and
   `AuctionItem.current_price` (auction) are the values `PaymentRequest.amount`
   copies at request creation. Neither changes after completion.
10. **Currency:** no currency on assets, orders, auction items, or payment
    requests. Only `CustomerProfile`/`SellerProfile.currency` (`CurrencyOptions`)
    exist.
11. `PaymentRequestService.approve()` is the only code path that sets
    `approved` or `OrderItem.completed`; it is only called from the payments
    decision view.

### App location decision

New app `apps/deals`. No existing app fits both deal types: `apps.orders` is
market-only, `apps.auctions` is auction-only, `apps.insights` is analytics, and
`apps.payments` is the confirmation workflow this ticket separates from.

### Architecture (implemented)

```text
Manual confirmation workflow   (apps.payments PaymentRequest: pending -> approved)
        |
        v
trusted successful finalization (PaymentRequestService.approve: stock, ownership,
        |                        order item completed, cashback earned)
        v
CompletedDeal                  (apps.deals, same atomic transaction)
        |
        v
future business events / RewardEngine (BE-REW-05)
```

- `PaymentRequest` = manual agreement/confirmation workflow.
- `CompletedDeal` = canonical successful business transaction.
- `CompletedDeal` is **not** proof of an electronic payment. PPW moves no money.

### Model (`apps/deals/models.py`)

| Field | Meaning |
| --- | --- |
| `deal_type` | `auction` or `market` |
| `source_type`, `source_id` | Business source: `auction_item` / `order_item` + its pk |
| `buyer_user`, `buyer_profile` | Buyer account and the mode used (`PROTECT`) |
| `seller_user`, `seller_profile` | Seller account and the mode used (`PROTECT`) |
| `asset` | Primary asset sold (`PROTECT`) |
| `amount` | Snapshot: `OrderItem.total_price` (market) or `AuctionItem.current_price` (auction) |
| `currency` | Snapshot of the seller profile's `currency` at completion |
| `completed_at` | The confirmation's `approved_at` |
| `metadata` | Market: `order_id`, `quantity`, `payment_request_id`. Auction: `auction_id`, `paired_bird_id`, `payment_request_id` |

Plus the standard `BaseModel` timestamps and soft-delete fields.

- The source is the business unit (order item / auction item), not the
  `PaymentRequest`, so the record stays valid if the confirmation workflow
  changes. The confirming request id is kept in `metadata` as a trace.
- Amount and currency are copied at creation and never recalculated.
  `amount` equals `PaymentRequest.amount`, which copies the same field.

### Constraints and indexes (migration `apps/deals/migrations/0001_initial.py`)

- `deals_unique_completed_deal_source`: unique `(source_type, source_id)`. One
  deal per business source, enforced by the database.
- `deals_completed_deal_type_matches_source`: `market` must use `order_item`,
  `auction` must use `auction_item`.
- `deals_completed_deal_amount_gte_0`: `amount >= 0`.
- Indexes: `(buyer_profile, completed_at)`, `(seller_profile, completed_at)`,
  `(deal_type, completed_at)`, plus Django's FK indexes.

### Service (`apps/deals/services.py`)

- `CompletedDealService.create_from_market_completion(order_item)`: requires
  `OrderItem.completed` and an approved `MarketPaymentRequest` for that item.
- `CompletedDealService.create_from_auction_completion(auction_item)`: requires
  `AuctionItem.sold` with a winner and an approved `AuctionPaymentRequest` for
  that item.
- Both resolve buyer/seller from the business source, check they match the
  confirmation, check each profile belongs to its account
  (`CompletedDealValidator.validate_parties`), and snapshot the amount.
- Idempotent: an existing deal for the source is returned. The create runs in a
  savepoint and falls back to the existing row on `IntegrityError`, so the
  unique constraint is the real guard.
- Query helpers: `completed_for_buyer(profile_or_account)` and
  `completed_for_seller(profile_or_account)`.
- It never changes payment status, approves requests, moves money, or creates
  payment records.

### Trigger point

`PaymentRequestService.approve()` records the deal right after saving the request
as `approved`, before logging and notifications, all inside the existing
`transaction.atomic()` block. Since BE-REW-05, recording a new deal also emits
the `DEAL_COMPLETED` rewards there (section 6). If finalization or any later step fails,
the deal rolls back with it. Seller order-item approval, payment request
creation, rejection, resubmission, auction settlement, and buy-now create no
deal.

### Immutability

- `CompletedDeal.save()` refuses updates and `delete()` refuses soft and hard
  deletes (`django.core.exceptions.ValidationError`). Profiles, users, and the
  asset are `PROTECT`ed.
- Read-only Django admin (no add/change/delete). No API.
- QuerySet-level `update()`/`delete()` bypass the model guard; nothing in the
  application calls them on this model.

### Reward behavior

Unchanged in BE-REW-04. BE-REW-05 moved the trigger to `DEAL_COMPLETED` without
changing the rewards (section 6).

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/deals/` (`__init__.py`, `apps.py`, `models.py`, `validators.py`, `services.py`, `admin.py`, `migrations/0001_initial.py`) | New app | Neutral completed-deal boundary |
| `ppw/settings/base.py` | Added `apps.deals` to `APPS` | Register the app |
| `apps/payments/services.py` | `approve()` records the deal; `_approve_market_request` / `_approve_auction_request` return the finalized source | Hook into the trusted completion |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | 11 Arabic entries (only those differ in the recompiled `.mo`) | Arabic-first messages |
| `tests/deals/test_completed_deals.py` | New, 17 tests | BE-REW-04 coverage |

### Tests (`tests/deals/test_completed_deals.py`)

Market and auction completion create one deal with correct type, source,
parties, asset, amount, currency, time, and metadata; repeated service calls
and second approvals do not duplicate; request creation, rejection, seller
order-item approval, and auction sale alone create none; a resubmitted request
creates one deal on approval; the service rejects sources without an approved
confirmation; a stock failure and a failure after deal creation both leave no
deal (request stays pending, stock unchanged); profile/account attribution is
correct and mismatches are rejected; the amount does not follow later price
changes; updates, deletes, duplicate sources, and type/source mismatches are
refused; the admin is read-only; query helpers work by profile and account;
market reward keys and badges are unchanged.

### Test results (2026-09-27, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| `pytest tests/payments -q` | 1 failed / 45 passed | 1 failed / 45 passed |
| `pytest tests/loyalty -q` | 7 failed / 95 passed | 102 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/orders tests/auctions tests/complaints tests/seasons tests/ratings -q` | 19 failed / 241 passed | 260 passed |
| `pytest -q` | 47 failed / 852 passed | 10 failed / 889 passed |
| `makemigrations --check --dry-run` | No changes detected | |

All failures are pre-existing: the full-suite failure sets are identical to the
baseline taken before BE-REW-02. The payments failure is
`test_unrelated_customer_cannot_retrieve_payment_request_detail`; the others are
English-message assertions under the current Arabic test settings.

### Remaining risks

- **No backfill.** Deals approved before BE-REW-04 have no `CompletedDeal`.
  Anything that switches to counting `CompletedDeal` (rewards, levels, seasons)
  will undercount history until an idempotent backfill runs over approved
  `PaymentRequest`s. The service can be reused for it because it is idempotent.
- **Currency is the seller's declared profile currency.** PPW has no
  per-listing or per-transaction currency.
- **Same-account deals are recorded.** `AGENTS.md` notes a user's customer mode
  is not explicitly blocked from bidding on the same user's seller auction, so
  `buyer_user == seller_user` is possible for auction deals. Reward consumers
  should decide whether such deals count.
- Tests run on SQLite, which does not enforce `select_for_update()` row locks.

## 6. Decouple RewardEngine From PaymentRequest (BE-REW-05)

Status: **DONE**

### Pre-change findings

`apps/loyalty/reward_engine.py` coupling to `apps.payments`:

1. `handle_event()` dispatches `auction_payment_request_approved` and
   `market_payment_request_approved`.
2. `_handle_auction_payment_approved` / `_handle_market_payment_approved`
   import `PaymentRequest`, `PaymentRequestStatus`, `PaymentRequestType`, load
   the request from context, and re-check `request_type` and `status=approved`.
3. First-sale checks count approved `PaymentRequest` rows per seller and type.
4. `_grant_auction_completion_extras` reaches
   `payment_request.auctionpaymentrequest.auction_item`;
   `_grant_order_completion_points` reaches
   `payment_request.marketpaymentrequest.order_item.total_price`.
5. `evaluate_trusted_buyer` / `evaluate_trusted_seller` count approved
   `PaymentRequest` rows.
6. The only caller is `PaymentRequestService._handle_approved_rewards()`, run
   synchronously at the end of `approve()` inside its atomic block.

Rewards currently triggered by approval (preserved by this ticket):

| Deal | Recipient | Reward | Idempotency key |
| --- | --- | --- | --- |
| Auction | Buyer | `AUCTION_PURCHASE_CONFIRMED` badge + 50 PP per deal (badge once per profile) | `reward:AUCTION_PURCHASE_CONFIRMED:auction_payment_request_approved:PaymentRequest:{pr}:profile:{buyer}` |
| Auction | Buyer | Lucky Wheel attempt | `lucky_wheel:auction_completed:{auction_item}:{buyer}` |
| Auction | Buyer account | 200 PP `first_bid_completed`, once per account | `points:first_bid_completed:user:{user}` |
| Auction | Seller | `FIRST_AUCTION_SALE_CONFIRMED` badge + 75 PP on the first auction sale | `reward:FIRST_AUCTION_SALE_CONFIRMED:auction_payment_request_approved:PaymentRequest:{pr}:profile:{seller}` |
| Market | Buyer | `MARKET_PURCHASE_CONFIRMED` badge + 30 PP per deal (badge once per profile) | `reward:MARKET_PURCHASE_CONFIRMED:market_payment_request_approved:PaymentRequest:{pr}:profile:{buyer}` |
| Market | Buyer and seller | `int(total_price)` PP + 100 PP bonus each | `points:order_item:{item}:{customer\|seller}:amount` / `:bonus` |
| Market | Seller | `FIRST_MARKET_SALE_CONFIRMED` badge + 50 PP on the first market sale | `reward:FIRST_MARKET_SALE_CONFIRMED:market_payment_request_approved:PaymentRequest:{pr}:profile:{seller}` |
| Both | Buyer / seller | Trusted buyer/seller Celery checks enqueued on commit (10 approved deals, 90-day badge, 100/150 PP) | `reward:TRUSTED_*:trusted_*_aggregate:Profile:{p}:profile:{p}` |

Historical ledger identity: badge-reward points store
`source_type="PaymentRequest"`, `source_id=<payment request id>`, metadata
`{"reward_code", "event_type"}`. Direct points store `source_type` =
reward code and `source_id` = order item / payment request id. `PointService`
rejects replaying a key with a different payload, so compatible keys must also
keep identical payloads.

`CompletedDeal` maps back to its source via `source_type`/`source_id`
(order item / auction item) and `metadata.payment_request_id`.

**Legacy double-award risk found:** first-sale rewards fire when the seller's
approved count equals 1, and `_grant_reward` pays points before the badge
check. Counting `CompletedDeal` instead would make a seller's first
post-BE-REW-04 deal count as 1 even if they had approved sales before, paying
the first-sale PP again under a new payment request key.

### Architecture (implemented)

```text
Before:  PaymentRequestService.approve() -> RewardEngine(*_payment_request_approved,
         context=PaymentRequest) -> engine re-checks status/type, walks
         payment request -> order item / auction item

After:   Manual confirmation (PaymentRequest approved)
               |
               v
         CompletedDeal            (CompletedDealService, same transaction)
               |
               v
         DEAL_COMPLETED           (emitted once, only when a new deal is recorded)
               |
               v
         RewardEngine._handle_deal_completed(deal)
               |
               v
         PointService / BadgeService / LuckyWheelGrantService / trusted-aggregate tasks
```

- `RewardEngine` no longer interprets `PaymentRequest` status or type, and
  `apps/loyalty/` imports nothing from `apps.payments`.
- `CompletedDeal` is the completion source of truth. `PaymentRequest` remains
  the manual workflow only; `apps.payments` no longer calls the reward engine.
- Rewards still run synchronously inside the approval transaction, so they
  roll back with it (checked by a test that fails after the deal and its
  rewards are written). Trusted aggregates are still enqueued with
  `transaction.on_commit()`.
- Branching uses `CompletedDeal.deal_type`: auction deals grant the auction
  purchase badge, Lucky Wheel attempt (via `AuctionItem` from `source_id`),
  `first_bid_completed`, and first auction sale; market deals grant the market
  purchase badge, `int(deal.amount)` + 100 PP to both sides, and first market
  sale. (Superseded by BE-REW-08: both deal types now pay the shared
  completed-deal reward and bonus; section 9.)
- Market amount points use the `CompletedDeal.amount` snapshot, not the
  mutable `OrderItem`.
- `evaluate_trusted_buyer` / `evaluate_trusted_seller` count `CompletedDeal`
  rows for the profile (thresholds, validity, and points unchanged).

### Event vocabulary

`RewardEventType` now includes `account_onboarded` (onboarding PP only, no
badges) and `deal_completed`. Still valid and unchanged: `customer_profile_created`,
`seller_profile_created`, `auction_published`, `market_item_listed`,
`bid_placed`, `rating_created`, `seller_followed`. Registration and mode creation
keep using the profile-created events because they also award welcome badges.

`auction_payment_request_approved` and `market_payment_request_approved` are
deprecated: `handle_event()` no longer dispatches them (an old-style call does
nothing), so there is exactly one completion reward path. The constants remain
because historical reward keys contain them.

### Idempotency compatibility

- Deals confirmed by a payment request (`metadata.payment_request_id` present)
  keep the exact pre-BE-REW-05 reward identity:
  `reward:{code}:{auction|market}_payment_request_approved:PaymentRequest:{payment_request_id}:profile:{id}`,
  with the same `source_type="PaymentRequest"`, `source_id`, reason, and metadata.
  Order-item points (`points:order_item:{item}:{role}:{amount|bonus}`),
  `points:first_bid_completed:user:{user}`, and the Lucky Wheel key
  (`lucky_wheel:auction_completed:{auction_item}:{profile}`) are also unchanged.
  An already-rewarded sale therefore hits its existing rows, and because the
  payload is identical `PointService` returns them instead of raising.
- A golden run of two auction deals and two market deals produced
  byte-identical ledger rows, badges, Lucky Wheel grants, and balances before
  and after the refactor.
- Deals without a payment request id get a neutral identity:
  `reward:{code}:deal_completed:{order_item|auction_item}:{source_id}:profile:{id}`.
- First-sale guard: `FIRST_*_SALE_CONFIRMED` pays only when this is the
  seller's first `CompletedDeal` of that type **and** the seller does not
  already hold that badge. Sellers whose first sale predates `CompletedDeal`
  hold the badge, so they are not paid again. Side effect: if a manager manually
  awarded that badge earlier, the first deal no longer pays its points (the old
  code paid them anyway).
- A retried `CompletedDealService` call returns the existing deal and does not
  emit `DEAL_COMPLETED` again; a repeated `DEAL_COMPLETED` for the same deal is
  a no-op through the unchanged keys.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/loyalty/reward_engine.py` | Added `_handle_deal_completed`, `_deal_reward_identity`, `_is_first_sale`, `RewardSource`, legacy identity constants; removed both payment handlers; extras/order points read the deal; trusted aggregates count `CompletedDeal`; `ACCOUNT_ONBOARDED` dispatch | Consume the neutral deal |
| `apps/loyalty/constants.py` | Added `ACCOUNT_ONBOARDED`, `DEAL_COMPLETED`; marked payment-approved events deprecated | Event vocabulary |
| `apps/deals/services.py` | Emits `DEAL_COMPLETED` when a new deal is recorded | Single trigger |
| `apps/payments/services.py` | Removed `_handle_approved_rewards` | Payments no longer drive rewards |
| `tests/factories.py` | Added `CompletedDealFactory` | Deal-based tests |
| `tests/loyalty/test_deal_completed_rewards.py` | New, 16 tests | BE-REW-05 coverage |
| `tests/loyalty/test_reward_engine.py` | Trusted aggregate tests build `CompletedDeal`s instead of approved `PaymentRequest`s; dropped unused imports | Source of truth changed |
| `tests/deals/test_completed_deals.py` | Rollback test now fails at `_log_event` and also asserts no rewards remain | The patched hook was removed |
| `AGENTS.md`, `docs/rewards/rewarding-system-report.md` | Rewards described as `deal_completed`-driven | No contradictions |

### Tests added (`tests/loyalty/test_deal_completed_rewards.py`)

- Market and auction completion through the real flow produce exactly the
  current ledger rows (keys, users, points) and one Lucky Wheel grant.
- Rejected requests create no deal and no rewards.
- Deals without any `PaymentRequest` in the database are rewarded (neutral
  keys); this is the behavioral guard that the engine needs no payment object.
- Repeated `DEAL_COMPLETED` and a retried deal-service call create no
  duplicate points, badges, or Lucky Wheel grants.
- An old-style `market_payment_request_approved` event does nothing.
- A deal whose sale was rewarded under legacy keys (backfill scenario) adds no
  rows and does not change balances.
- A seller holding a pre-deal first-sale badge is not paid first-sale PP again.
- Trusted buyer ignores 10 approved `PaymentRequest`s without deals and awards
  at the 10th `CompletedDeal`; trusted seller likewise; completion enqueues both
  aggregate tasks on commit.
- Market amount points use the deal snapshot after the item and asset prices
  change.
- Rewards go to the deal's accounts and modes (a buyer account with a second
  seller mode keeps customer-profile attribution).
- `ACCOUNT_ONBOARDED` pays onboarding once and awards no badge.
- A paired-bird auction deal still grants one Lucky Wheel attempt.

Mutation check: disabling the legacy identity fails 3 of these tests; disabling
the first-sale badge guard fails 1.

### Test results (2026-09-27, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/loyalty/test_deal_completed_rewards.py -q` | 16 passed | 16 passed |
| `pytest tests/loyalty -q` | 7 failed / 111 passed | 118 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/payments -q` | 1 failed / 45 passed | 1 failed / 45 passed |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| BE-REW-02 + BE-REW-03 + BE-REW-05 test files | 53 passed | 53 passed |
| `pytest -q` | 47 failed / 868 passed | 10 failed / 905 passed |
| `makemigrations --check --dry-run` | No changes detected | |

No new failures: both full-suite failure sets are identical to the pre-BE-REW-02
baseline (the 7 loyalty failures are English-message assertions under the
current Arabic test settings; the payments failure is the pre-existing
`test_unrelated_customer_cannot_retrieve_payment_request_detail`).

### Migration status

None. `CompletedDeal` already stores everything the handler needs (`deal_type`,
source, parties, amount, `metadata.payment_request_id`).

### Remaining risks

- **Legacy counts until backfill.** Deals approved before BE-REW-04 have no
  `CompletedDeal`, so trusted buyer/seller thresholds are reached later than
  under the old `PaymentRequest` count (no double award, only a delay).
  An idempotent backfill would fix this. Backfilled deals replaying
  `DEAL_COMPLETED` are safe for per-deal rewards through the legacy keys; the
  first-sale guard relies on the badge rows.
- ~~The badge catalog still reported payment-based `criteria_type` values.~~
  Resolved in BE-REW-06 (section 7).
- The legacy labels (`PaymentRequest`, `*_payment_request_approved`) stay inside
  new reward keys and ledger `source_type` for payment-confirmed deals. This is
  deliberate for idempotency; new consumers should read `CompletedDeal`, not
  parse keys.
- Same-account auction deals (`buyer_user == seller_user`) are rewarded like any
  other deal, as before. Blocking that is a product decision.
- Tests run on SQLite, which does not enforce `select_for_update()` row locks.

## 7. Separate Badges From Point Rewards (BE-REW-06)

Status: **DONE**

### Pre-change findings

1. `BADGE_DEFINITIONS` puts a `points` value in `criteria_value` for 14 badges,
   including `WELCOME_BUYER: 100` and `WELCOME_SELLER: 100`, which have paid
   nothing since BE-REW-02. `GET /api/loyalty/badges/` and `/badges/my/` expose
   it. No test or backend code reads it.
2. `RewardEngine._grant_reward(badge_type, ...)` pays
   `REWARD_POINTS[badge_type]` and then awards the badge, unless the caller
   passes `award_points=False`. The badge code implies the payout.
3. Points + badge (intentional, current payouts): `FIRST_BIDDER` 10,
   `LAST_MINUTE_FIGHTER` 5 (also paid on every later last-minute bid while
   the badge is active), `AUCTION_PURCHASE_CONFIRMED` 50,
   `MARKET_PURCHASE_CONFIRMED` 30, `REVIEWER_BUYER` 10, `LOYAL_FOLLOWER` 5,
   `FIRST_AUCTION_PUBLISHED` 25, `FIRST_MARKET_ITEM_LISTED` 25,
   `FIRST_AUCTION_SALE_CONFIRMED` 75, `FIRST_MARKET_SALE_CONFIRMED` 50,
   `TRUSTED_BUYER` 100, `TRUSTED_SELLER` 150.
4. Badge-only: `WELCOME_BUYER`, `WELCOME_SELLER` (via `award_points=False`),
   and manager awards through `BadgeService.award_badge()`.
5. Points-only: account onboarding (`ACCOUNT_ONBOARDING` rule), auction
   posted 50, rating received 30, first bid completed 200, market deal amount
   and 100 bonus, referral inviter 30, season winners, cashback purchase,
   Lucky Wheel PP prizes.
6. Stale catalog metadata: yes (item 1).
7. Payment trigger wording: `AUCTION/MARKET_PURCHASE_CONFIRMED` and
   `FIRST_*_SALE_CONFIRMED` use `criteria_type` `*_payment_request_approved`
   and descriptions mentioning payment requests; `TRUSTED_*` use
   `approved_payment_request_count`.

A golden run of every badge path (welcome, bids, first auction/listing,
rating, follow, auction and market deals, trusted aggregates) was captured
before the refactor for a byte-level comparison.

### Responsibilities (implemented)

| Component | Owns |
| --- | --- |
| `BadgeService` | Badge ownership: `BadgeAward` rows, eligibility, validity, one-per-profile rules |
| `BADGE_DEFINITIONS` | Badge presentation and criteria only (name, description, eligible profile, criteria, icon). Not PP Coin configuration |
| `RewardRuleService` | Configurable PP Coin reward values (`RewardRuleConfig` + defaults) |
| `PointService` | PP Coin ledger mutation |
| `RewardEngine` | Orchestration: decides, per business event, which points and which badges to grant |

```text
Before:  _grant_reward(badge_type) -> REWARD_POINTS[badge_type] PP  +  badge
         (the badge code implied the payout unless award_points=False)

After:   Business event
            |-- _grant_points(profile, reward_code, points, ...)  -> PointService
            |       points from RewardRuleService or an explicit *_POINTS value
            |-- _award_badge(profile, badge_type, ...)             -> BadgeService
            '-- _grant_points_and_badge(profile, badge_type, points, ...)
                    only where the event intentionally grants both
```

- `REWARD_POINTS` is gone. The amounts that some events pay alongside a badge
  are explicit constants in `apps/loyalty/constants.py` (`FIRST_BID_POINTS`,
  `LAST_MINUTE_BID_POINTS`, `AUCTION_PURCHASE_POINTS`,
  `MARKET_PURCHASE_POINTS`, `FIRST_RATING_POINTS`,
  `FIRST_SELLER_FOLLOW_POINTS`, `FIRST_AUCTION_PUBLISHED_POINTS`,
  `FIRST_MARKET_ITEM_LISTED_POINTS`, `FIRST_AUCTION_SALE_POINTS`,
  `FIRST_MARKET_SALE_POINTS`, `TRUSTED_BUYER_POINTS`, `TRUSTED_SELLER_POINTS`),
  passed at each call site.
- They are not moved into `RewardRuleConfig` yet: adding codes to
  `RewardRuleCode` changes the model field's `choices`, which Django records
  as a migration, and this ticket required no migration.
- Welcome badges are badge-only. Account onboarding (`ACCOUNT_ONBOARDING`
  rule, default 100 PP, once per `UserAccount`) is the only onboarding PP.
- Last-minute bids: 5 PP on every last-minute bid, plus the 7-day
  `LAST_MINUTE_FIGHTER` badge only when none is active (unchanged).
- No new payments behavior.

### Behavior changes

- **Structural only for payouts.** Point amounts, badge awards, idempotency
  keys, ledger `source_type`/`source_id`/reason/metadata, balances, and event
  result dicts are byte-identical before and after (golden run over every
  badge path, plus the BE-REW-05 deal golden).
- **Corrected catalog content** in `BADGE_DEFINITIONS` (served by
  `/api/loyalty/badges/` and `/api/loyalty/badges/my/`):
  - `criteria_value` no longer contains `points` for any badge (it claimed
    100 PP for each welcome badge, which they have not paid since BE-REW-02).
  - Deal badges use neutral wording: `criteria_type` `auction_deal_completed`,
    `market_deal_completed`, `first_auction_deal_completed`,
    `first_market_deal_completed`, `completed_purchase_count`,
    `completed_sale_count`; descriptions say "completed purchase/sale".
  - Welcome descriptions say "Welcome badge" instead of "Starter reward".

### API impact

Field set of each catalog / earned-badge item is unchanged. Only the content of
`criteria_value`, `criteria_type`, and `description` changed. Example:

```text
WELCOME_BUYER    before: criteria_value {"points": 100}
                 after:  criteria_value {}
TRUSTED_SELLER   before: criteria_type "approved_payment_request_count",
                         criteria_value {"threshold": 10, "points": 150, "valid_days": 90}
                 after:  criteria_type "completed_sale_count",
                         criteria_value {"threshold": 10, "valid_days": 90}
```

No backend code or test read `criteria_value.points`; a client that displayed it
will now see nothing for that key.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/loyalty/constants.py` | Removed `REWARD_POINTS`; added explicit `*_POINTS` amounts; `BADGE_DEFINITIONS` without `points`, neutral deal wording | Badges are not point configuration |
| `apps/loyalty/reward_engine.py` | `_grant_reward` replaced by `_grant_points`, `_award_badge`, `_grant_points_and_badge`; every call site states its points | No implicit badge -> points |
| `tests/loyalty/test_badge_point_separation.py` | New, 12 tests | BE-REW-06 coverage |
| `docs/rewards/rewarding-system-report.md`, `AGENTS.md` | Badge tables and badge notes aligned | No contradictions |

### Tests (`tests/loyalty/test_badge_point_separation.py`)

Welcome buyer and seller badges are badge-only (onboarding 100 PP once, no
welcome point rows); badge-only and manager badge awards create no points;
points-only helper and the auction-posted reward create no badge; a
points + badge event creates exactly one of each and is idempotent on retry;
the combined helper retried creates no duplicates; current badge-paired payouts
(25, 10, 5+5, 5, 100, 150) are unchanged; the catalog has no `points` and no
payment wording, with the item field set unchanged; `/badges/my/` uses the
cleaned definitions. Mutation check: making the welcome buyer badge pay 100 PP
again fails the welcome test.

### Test results (2026-09-27, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/loyalty -q` | 7 failed / 123 passed | 130 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| `pytest tests/payments -q` | 1 failed / 45 passed | 1 failed / 45 passed |
| BE-REW-02..06 focused tests together | 82 passed | 82 passed |
| `pytest -q` | 47 failed / 880 passed | 10 failed / 917 passed |
| `makemigrations --check --dry-run` | No changes detected | |

No new failures: both full-suite failure sets are identical to the pre-BE-REW-02
baseline (known English-message assertions under the current Arabic test
settings, and the pre-existing payments detail-permission test).

### Migration status

None.

### Remaining risks

- The badge-paired point amounts are explicit code constants, not
  admin-configurable. Moving them into `RewardRuleService` needs new
  `RewardRuleCode` values (a choices-only migration) and a product decision on
  which rule each amount belongs to.
- The `reward_code` in badge-paired point keys is still the badge code (for
  example `reward:FIRST_BIDDER:...`). That is kept on purpose so historical
  idempotency keys stay valid; it names the reward, it does not make the badge
  pay.
- Clients that read `criteria_value.points` from the badge catalog lose that
  value.

## 8. Active Auction Bidder Reward (BE-REW-07)

Status: **DONE**

### Requirement

When an auction has at least `threshold` distinct bidder accounts (default 5),
every distinct bidder receives `points` PP (default 50) once for that auction.
Bidders who join after the threshold is reached receive it once when they bid.
Values come from the `ACTIVE_AUCTION_BIDDER` reward rule. Points only, no badge.

### Pre-change findings

1. `Bid` always belongs to `AuctionItem` (`Bid.item`); `AuctionItem` belongs to
   `Auction` (`Auction.items`). Multi-item auction types exist (`multi`,
   `pair`, `breeding`, `racing`), each item with its own bid stream and winner.
2. A bid becomes accepted in `BiddingService.place_bid()`: inside one
   `transaction.atomic()` it locks the `AuctionItem` row, runs
   `BidBusinessValidator`, creates the `Bid`, updates price / auto-extend, logs
   the `bid_placed` event, then calls `RewardEngine.handle_event(BID_PLACED)`
   synchronously. Failed validation raises before the bid is created, so
   rejected bids are never persisted and never emit `BID_PLACED`.
3. `Bid` has no status field; only soft delete (`deleted`). `Bid.objects`
   excludes soft-deleted bids.
4. `BidBusinessValidator` allows only `Customer` profiles to bid. A user has at
   most one customer profile (`unique_together (user, type)`), so a
   `UserAccount` bids through exactly one profile.
5. The validator does **not** block a user's customer profile from bidding on
   an auction owned by the same user's seller profile (also listed in
   `AGENTS.md` known risks).
6. Buy-now (`SettlementService.trigger_buy_now`) also creates a `Bid` row but
   emits no `BID_PLACED`, and it does not enforce the customer-only rule.
7. No existing reward counts distinct bidders. `FIRST_BIDDER` (first bid per
   profile) and `LAST_MINUTE_FIGHTER` (bids in the final 5 minutes) run in the
   same `BID_PLACED` handler.
8. `RewardRuleCode.ACTIVE_AUCTION_BIDDER` exists (BE-REW-03: 50 PP, threshold 5,
   `required_fields=("points", "threshold")`, `is_implemented=False`).
   `PointTransaction.source_type` is a plain `CharField` (no choices), so a new
   `RewardSourceType` value needs no migration.

### Implemented behavior

```text
BiddingService.place_bid()  (valid bid, inside its atomic block)
        |
        v
RewardEngine.handle_event(BID_PLACED)
        |-- first-bid / last-minute rewards (unchanged)
        v
_grant_active_auction_bidder_rewards(bid)
        |-- RewardRuleService.get_rule(ACTIVE_AUCTION_BIDDER): enabled, points, threshold
        |-- distinct bidder accounts of the auction >= threshold ?
        v
PointService.earn_points() for each not-yet-paid bidder account (points only)
```

Qualification rule:

- **Scope: auction level.** Bidders are counted across all items of the
  auction (`Bid.item.auction`), not per item.
- **Which bids count:** non-deleted `Bid` rows by `Customer` profiles on
  non-deleted items of the auction. Rejected bids are never persisted. Bid rows
  from non-customer profiles (possible through buy-now, which skips the
  customer check) do not count.
- **Distinct users:** counted by `bidder.user_id`. Any number of bids by one
  account counts once.
- Evaluation runs on every valid `BID_PLACED` (regular bids only; buy-now does
  not emit it). When the count reaches the threshold, every distinct bidder is
  paid; each later new bidder is paid on their own bid; already-paid accounts
  are skipped.
- Rule handling: disabled, `points` of 0/None, or `threshold` None -> no
  reward. Changed points or threshold apply to future awards only; existing
  rows are never rewritten. A lowered threshold takes effect on the next bid.

Ledger row per account and auction:

| Field | Value |
| --- | --- |
| `user` | bidder `UserAccount` (shared wallet) |
| `profile` | the bidding customer profile |
| `points` | rule points (default 50) |
| `reason` | `Active auction bidder reward` |
| `source_type` / `source_id` | `active_auction_bidder` / auction id |
| `idempotency_key` | `reward:active_auction_bidder:auction:{auction_id}:user:{user_id}` |
| `metadata` | `{"reward_code": "active_auction_bidder", "auction_id", "threshold"}` |

- No `BadgeAward` is created (points-only path).
- Idempotency is per auction and account, never per bid, so repeated bids
  cannot repeat the reward. Paid keys are read in one query before writing, so
  later bids never replay old rows (which avoids payload-mismatch errors after
  a config change). Each insert runs in a savepoint; a duplicate from a
  concurrent evaluation hits the unique key, rolls back only that savepoint, and
  is treated as already paid, so the bid itself still succeeds. Accounts are
  processed in user-id order, so concurrent evaluations lock wallets in the
  same order.
- `RewardSourceType.ACTIVE_AUCTION_BIDDER` was added; the rule default now has
  `is_implemented=True`.
- **No historical backfill.** Only bids placed after deployment trigger
  evaluation; an old auction that already had 5+ bidders pays everyone the
  next time a valid bid is placed on it while it is active.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/loyalty/reward_engine.py` | `_grant_active_auction_bidder_rewards`, `_paid_keys`, `active_auction_bidder_idempotency_key`; called from the `BID_PLACED` handler | The reward |
| `apps/loyalty/constants.py` | `RewardSourceType.ACTIVE_AUCTION_BIDDER`; rule default `implemented=True` | Ledger source and rule status |
| `tests/loyalty/test_active_auction_bidder_reward.py` | New, 19 tests | BE-REW-07 coverage |
| `tests/loyalty/test_reward_rule_config.py` | Default-rule test now expects `is_implemented=True` | The rule is now read |
| `AGENTS.md`, `docs/rewards/rewarding-system-report.md` | Rule now live | No contradictions |

### Tests (`tests/loyalty/test_active_auction_bidder_reward.py`)

4 bidders -> nothing; 5th bidder -> all 5 get 50; 22 bids from 4 accounts ->
nothing; 6th/7th/8th bidders each paid once, the first five unchanged;
bidders spread over two items of one auction count together; the same account
is paid once in each of two auctions; replayed `BID_PLACED` adds nothing;
points 75 and threshold 3 overrides apply; disabled rule pays nothing; a later
config change leaves earlier rows at 50; deleted bids, a seller-profile bid row,
and bids on a deleted item do not count; a rejected bid is not stored and pays
nothing; ledger attribution (user, profile, reason, source, key, metadata);
no badge created; the reward is visible in seller mode of the same account;
a simulated concurrent duplicate insert is swallowed with balances unchanged;
repeated evaluations keep one row per auction/account; first-bid (10 PP +
badge) and last-minute (5 PP per bid + one badge) rewards unchanged alongside it.

Mutation checks: counting bid rows instead of accounts fails 2 tests; dropping
the customer filter fails 1; removing the duplicate-insert handling fails 1.

### Test results (2026-10-02, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/loyalty/test_active_auction_bidder_reward.py -q` | 19 passed | 19 passed |
| `pytest tests/loyalty -q` | 7 failed / 142 passed | 149 passed |
| `pytest tests/auctions -q` | 9 failed / 71 passed | 80 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| BE-REW-02..07 focused tests together | 101 passed | 101 passed |
| `pytest -q` | 47 failed / 901 passed | 10 failed / 938 passed |
| `makemigrations --check --dry-run` | No changes detected | |

No new failures: both full-suite failure sets are identical to a baseline taken
at commit `66d7992` before this change (current-settings failures are
English-message assertions under the Arabic test settings; `en` failures are
the known chat, payments detail-permission, i18n cart, and migration tests).

### Migration status

None. `PointTransaction.source_type` has no choices, and the rule already
existed.

### Remaining risks

- **Self-bidding counts.** A user's customer profile can bid on an auction owned
  by the same user's seller profile, so that account counts toward the
  threshold and is paid. Blocking it is a bidding-policy decision (already an
  `AGENTS.md` known risk).
- **Bid farming.** Five accounts each placing one small valid bid unlock 50 PP
  each. The rule has no minimum bid amount or account-age condition.
- Buy-now bids count as participation when they come from a customer profile
  but do not trigger evaluation themselves.
- Each bid on a qualifying auction runs one grouped bidder query and one
  paid-keys query; cost grows with the number of distinct bidders only when
  new bidders need paying.
- Tests run on SQLite, which does not enforce `select_for_update()` row locks;
  the concurrent path was exercised by simulating stale reads, not by real
  parallel transactions. Verify on PostgreSQL.

## 9. Completed Deal Double Points (BE-REW-08)

Status: **DONE**

### Product decision (2026-10-02)

The decision gate below was resolved by the product owner:

```text
base_points = floor(CompletedDeal.amount × points_per_currency_unit[deal.currency])
deal_points = base_points × DEAL_COMPLETED.multiplier          (default multiplier 2)

buyer  account += deal_points
seller account += deal_points
```

- Permanent shared completed-deal incentive; **not** the seasonal Marketing
  Offers "double points" campaign.
- Applies to **market and auction** deals, from `CompletedDeal.amount`.
- Currency rates are configured on the `DEAL_COMPLETED` rule; initial rates
  EGP = 1 PP/unit and USD = 1 PP/unit (original specification). Any other or
  missing currency is **not** assumed 1:1: the deal points are skipped, logged
  as a warning, and reported in the event result.
- Rounding: floor the base **before** multiplying
  (250.75 EGP -> floor(250.75) = 250 -> 250 × 2 = 500 PP, not 501).
- The +100 full-process completion bonus stays a **separate** rule, is **not**
  doubled, and applies to both buyer and seller of market and auction deals.
  It is not coupled to badges.
- Not doubled or changed: badge-paired PP, first-time PP, Lucky Wheel,
  trusted buyer/seller, referral, and active auction bidder rewards.
- No backfill, compensation, or recalculation. A deal already rewarded under
  the legacy amount formula must not be paid again on replay.
- Trigger stays `CompletedDeal -> DEAL_COMPLETED -> RewardEngine`.

### Implemented behavior

```text
CompletedDeal -> DEAL_COMPLETED -> RewardEngine._handle_deal_completed(deal)
   |-- first-processing check (purchase-reward key not yet written for this deal)
   |-- _grant_completed_deal_points:  RewardRuleService(DEAL_COMPLETED)
   |       rate = configuration["points_per_unit_<deal.currency>"]
   |       base_points = floor(deal.amount × rate)
   |       deal_points = floor(base_points × multiplier)
   |       PointService -> buyer account, PointService -> seller account
   |-- _grant_completed_deal_bonus:   RewardRuleService(DEAL_COMPLETION_BONUS)
   |       bonus points -> buyer account, seller account
   |-- purchase badge PP, first-sale badge PP, auction extras (unchanged)
   '-- trusted buyer/seller aggregates on commit (unchanged)
```

Exact formula (per party, buyer and seller each):

```text
base_points = floor(CompletedDeal.amount × points_per_unit[CompletedDeal.currency])
deal_points = floor(base_points × DEAL_COMPLETED.multiplier)
bonus       = DEAL_COMPLETION_BONUS.points
```

The outer floor only matters for a fractional multiplier; with the default 2 it
is exact (the base is floored before multiplying).

Defaults:

| Rule | Values |
| --- | --- |
| `DEAL_COMPLETED` | `multiplier=2`, `configuration={"points_per_unit_EGP": 1, "points_per_unit_USD": 1}`, required `multiplier` |
| `DEAL_COMPLETION_BONUS` (new) | `points=100`, required `points` |

Examples (EGP, defaults):

| Deal amount | base | deal points (each) | bonus (each) |
| --- | ---: | ---: | ---: |
| 100.00 | 100 | 200 | 100 |
| 250.75 | 250 | 500 (not 501) | 100 |
| 99.99 | 99 | 198 | 100 |
| 0.99 | 0 | 0 (no row) | 100 |
| 250.75 with EGP rate 2 | floor(501.5) = 501 | 1002 | 100 |
| 100.00 SAR (no rate) | n/a | 0, skipped and logged | 100 |

Payouts per completed deal, after BE-REW-08:

| Deal | Party | Deal points | Bonus | Unchanged rewards |
| --- | --- | --- | --- | --- |
| Market | Buyer | `deal_points` | 100 | 30 PP + `MARKET_PURCHASE_CONFIRMED` badge |
| Market | Seller | `deal_points` | 100 | first market sale 50 PP + badge; since BE-REW-09 also successful sale 100 PP (section 10) |
| Auction | Buyer | `deal_points` (new) | 100 (new) | 50 PP + `AUCTION_PURCHASE_CONFIRMED` badge; 200 PP `first_bid_completed` once per account; Lucky Wheel attempt |
| Auction | Seller | `deal_points` (new) | 100 (new) | first auction sale 75 PP + badge; since BE-REW-09 also successful sale 100 PP (section 10) |

Replaced payouts: the market `int(amount)` row (`order_completed_amount`) and the
market 100 PP row (`order_completed_bonus`) are no longer written; their
replacements are the two new rules. Not doubled or changed: badge-paired PP,
first-time PP, Lucky Wheel, trusted buyer/seller, referral, active auction
bidder, onboarding.

Ledger rows:

| | Deal points | Bonus |
| --- | --- | --- |
| `idempotency_key` | `reward:completed_deal:{source_type}:{source_id}:{buyer\|seller}` | `reward:completed_deal_bonus:{source_type}:{source_id}:{buyer\|seller}` |
| `source_type` / `source_id` | `deal_completed` / `CompletedDeal.id` | `deal_completion_bonus` / `CompletedDeal.id` |
| `reason` | `Completed deal reward` | `Completed deal bonus` |
| `user` / `profile` | `deal.buyer_user` / `deal.buyer_profile` (buyer row); `deal.seller_user` / `deal.seller_profile` (seller row) | same |
| `metadata` | `reward_code`, `deal_id`, `deal_type`, `currency`, `rate`, `base_points`, `multiplier` | `reward_code`, `deal_id`, `deal_type` |

Rules and configuration:

- `DEAL_COMPLETED` disabled -> no deal points; the bonus, badges, Lucky Wheel,
  first-time rewards, and trusted aggregates still run.
  `DEAL_COMPLETION_BONUS` disabled -> no bonus; deal points still run.
- Rates are flat `configuration` keys `points_per_unit_<CURRENCY>`; the manager
  API rejects non-numeric, boolean, or negative rate values. A deal whose
  currency has no valid rate (or is blank) gets no deal points: a warning is
  logged (`apps.loyalty.reward_engine`) and the event result lists it under
  `skipped` with reason `unconfigured_currency`. The deal is never assumed
  1:1, and the approval still succeeds.
- Config changes affect only deals rewarded afterwards.

Idempotency and legacy compatibility:

- Old keys (pre-BE-REW-08, market only):
  `points:order_item:{order_item_id}:{customer|seller}:amount` and `...:bonus`.
  New keys: the two `reward:completed_deal...` keys above.
- First-processing gate: the shared deal points and bonus are paid only when
  the buyer's purchase-reward row for this deal does not exist yet at the start
  of the handler. Every deal rewarded by earlier code (BE-REW-05/06) or by this
  code writes that row, keyed by the deal's own identity
  (`reward:{AUCTION|MARKET}_PURCHASE_CONFIRMED:...:PaymentRequest:{id}:profile:{buyer}`
  or the neutral `deal_completed` form). So a replayed or backfilled deal that was
  already rewarded under the legacy formula, while a rule was disabled, or with
  an unconfigured currency, gets nothing new. This is the "no recalculation, no
  compensation" rule.
- Each new row is also guarded by its own key (checked before writing, plus a
  savepoint that treats a concurrent duplicate insert as already paid).
- BE-REW-05 identities for the purchase, first-sale, first-bid, and Lucky Wheel
  rewards are unchanged.

Other notes:

- Rounding: floor (current behavior, now explicit); no document defines another
  rule.
- Same-account deals (`buyer_user == seller_user`) pay both roles into the one
  wallet, as before; not blocked here.
- No backfill, no compensation, no recalculation of historical deals.
- `RewardResult` gained a `skipped` list, included in `handle_event()` results.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/loyalty/reward_engine.py` | `_grant_completed_deal_points`, `_grant_completed_deal_bonus`, `_deal_points_rate`, `_grant_deal_reward_once`, first-processing gate; removed `_grant_order_completion_points`; `RewardResult.skipped` | Shared completed-deal reward for both deal types |
| `apps/loyalty/constants.py` | `DEAL_COMPLETED` defaults (multiplier 2, EGP/USD rates, implemented); new `DEAL_COMPLETION_BONUS` rule (100 PP); `RewardSourceType.DEAL_COMPLETED` / `DEAL_COMPLETION_BONUS`; `DEAL_POINTS_RATE_KEY_PREFIX`; `_reward_rule` accepts multiplier/configuration | Configuration through RewardRuleService |
| `apps/loyalty/serializers.py` | Rate keys must be non-negative numbers | Safe admin configuration |
| `apps/loyalty/migrations/0004_reward_rule_deal_completion_bonus.py` | `AlterField` for the new `RewardRuleConfig.code` choice; `sqlmigrate` shows `(no-op)` on PostgreSQL | Django records choices |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | 2 Arabic entries (only those differ) | Arabic-first text |
| `tests/factories.py` | `CompletedDealFactory.currency = EGP` | Factory deals use a configured currency |
| `tests/loyalty/test_completed_deal_points.py` | New, 30 tests | BE-REW-08 coverage |
| `tests/loyalty/test_deal_completed_rewards.py`, `tests/deals/test_completed_deals.py` | Expected market/auction payouts and keys updated to the new formula | Intended behavior change |
| `tests/loyalty/test_reward_rule_config.py` | `DEAL_COMPLETED` no longer listed as pending | Rule implemented |
| `tests/loyalty/test_reward_engine.py` | Unknown-event result includes `skipped: []` | New result key |
| `AGENTS.md`, `docs/rewards/rewarding-system-report.md` | Deal payout text | No contradictions |

### Tests (`tests/loyalty/test_completed_deal_points.py`)

Defaults; market buyer and seller rows (points, profile, reason, keys) and
balances; auction buyer and seller; floor before multiplier (250.75 -> 500,
99.99 -> 198, 0.99 -> 0); rate applied before floor (rate 2 -> 1002);
fractional multiplier floors the result; amount from the deal snapshot after
the auction item price changes; USD rate; unconfigured SAR and blank currency
skipped with warning and `skipped` entry while the bonus is still paid; adding
a SAR rate enables it; the API rejects invalid rates; multiplier 3; disabled deal
rule keeps bonus, badges, and Lucky Wheel; disabled bonus rule keeps deal
points; bonus points configurable; replay x3 pays once; config change does not
recalculate a rewarded deal; a deal rewarded while the rule was disabled is not
compensated; legacy-rewarded market and auction deals get nothing new; first-bid
and first-sale rewards unchanged on a second deal while deal points and bonus
repeat per deal; trusted aggregates still count deals; shared wallet in seller
mode; same-account deal pays both roles.

Mutation checks: flooring after the multiplier fails 18 tests; removing the
first-processing gate fails 4 (including BE-REW-05 legacy tests); assuming 1:1
for an unconfigured currency fails 2.

### Test results (2026-10-02, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/loyalty/test_completed_deal_points.py -q` | 30 passed | 30 passed |
| `pytest tests/loyalty -q` | 7 failed / 172 passed | 179 passed |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/payments -q` | 1 failed / 45 passed | 1 failed / 45 passed |
| BE-REW-02..08 focused tests together | 131 passed | 131 passed |
| `pytest -q` | 47 failed / 931 passed | 10 failed / 968 passed |
| `makemigrations --check --dry-run` | No changes detected | |

No new failures: both full-suite failure sets are identical to the baseline at
commit `66d7992` (English-message assertions under the current Arabic test
settings; known chat, payments detail-permission, i18n cart, and migration
tests under `en`).

### Migration status

`apps/loyalty/migrations/0004_reward_rule_deal_completion_bonus.py`: choices-only
`AlterField` for the new `DEAL_COMPLETION_BONUS` code. No table change
(`sqlmigrate` prints `(no-op)` on PostgreSQL).

### Remaining risks

- **Only EGP and USD are configured.** Deals by sellers with any other profile
  currency (SAR, AED, KWD, ...) earn no deal points until a manager adds a rate;
  this is logged per deal. The bonus is still paid.
- **Value per currency.** 1 PP per EGP and 1 PP per USD are worth very
  different amounts; that is the specified rule.
- **Large auction payouts.** Auctions now pay amount-based points: a 10,000 EGP
  auction pays 20,000 PP to each side plus the bonus.
- **Self-deals and small deals.** Same-account deals pay both roles to one
  wallet, and small coordinated deals collect the 100 PP bonus each. No
  anti-farming rule exists.
- The first-processing gate relies on the buyer's purchase-reward row being
  written for every rewarded deal; changing that reward's identity later would
  need a new gate.
- Tests run on SQLite, which does not enforce `select_for_update()` row locks.

### Future alignment item (not part of BE-REW-08)

The original points table defines a **100 PP "successful sale" reward**. The
current first-sale rewards (`FIRST_AUCTION_SALE_CONFIRMED` 75 PP,
`FIRST_MARKET_SALE_CONFIRMED` 50 PP, first sale only) do not match it. Not
changed in BE-REW-08. **Resolved by BE-REW-09** (section 10) as a separate,
recurring seller-only `SUCCESSFUL_SALE` reward; the first-sale incentives were
kept unchanged alongside it.

### Decision gate record (2026-09-27, before the product decision)

### Requirement sources found

- `backend remainings/REQUIREMENTS_AUDIT_SECTION_6.md` (branch
  `backend-changelogs-and-updates`, commit `b5d017a`; not on this branch),
  §6.2 Interactive / Shared Rewards:
  - 6.2.1 "Double points for both parties when seller + buyer complete a deal"
  - 6.2.2 "50 points per bid in an active auction with 5+ bidders" (BE-REW-07)
  - 6.2.3 "20 points for each party on a mutual review/rating"
- The same audit, §6.8 Marketing Offers, lists "double points" as a seasonal
  campaign offer, and §6.8.5 "Double-points offer type (points multiplier)".
  `REQUIREMENTS_AUDIT_SECTION_11 & Payments.md` §7 models it as a time-bounded
  `MarketingOffer` with `offer_type=double_points` and a `value` multiplier
  hooked into `PointService.earn_points()`.
- No document in this branch, its history, or `backend-changelogs-and-updates`
  defines a PP Coin earning table, the base amount that 6.2.1 doubles, a
  points-per-currency rate, or a rounding rule. These audits are summaries of the
  original Arabic product specification, which is not in the repository.

**Conclusion: the formula is not defined by any available source.**

### Current implemented payouts (unchanged)

| Deal | Party | PP today | Category | Badge | Other |
| --- | --- | --- | --- | --- | --- |
| Market | Buyer | 30 PP every deal (`MARKET_PURCHASE_POINTS`) | C. badge-paired | `MARKET_PURCHASE_CONFIRMED` (once per profile) | Trusted-buyer aggregate enqueued |
| Market | Buyer | `int(deal.amount)` PP (`order_completed_amount`) | A. base deal reward | none | |
| Market | Buyer | 100 PP (`order_completed_bonus`) | A. base deal bonus | none | |
| Market | Seller | `int(deal.amount)` PP + 100 PP | A. base deal reward + bonus | none | Trusted-seller aggregate enqueued |
| Market | Seller | 50 PP on first market deal (`FIRST_MARKET_SALE_POINTS`) | B/C. first-time, badge-paired | `FIRST_MARKET_SALE_CONFIRMED` | |
| Auction | Buyer | 50 PP every deal (`AUCTION_PURCHASE_POINTS`) | C. badge-paired | `AUCTION_PURCHASE_CONFIRMED` (once per profile) | Lucky Wheel attempt (D); trusted-buyer aggregate (E) |
| Auction | Buyer | 200 PP once per account (`first_bid_completed`) | B. first-time | none | |
| Auction | Seller | 75 PP on first auction deal (`FIRST_AUCTION_SALE_POINTS`) | B/C. first-time, badge-paired | `FIRST_AUCTION_SALE_CONFIRMED` | Trusted-seller aggregate (E) |
| Auction | Either | **no amount-based reward, no bonus** | | | |

- `int(deal.amount)` truncates; amounts are non-negative, so this is floor
  (99.90 -> 99). No document defines rounding.
- Amounts are in the seller profile's currency with no conversion
  (`CompletedDeal.currency`), so 1 PP per unit means very different value in
  EGP vs KWD.
- `DEAL_COMPLETED` reward rule (BE-REW-03) is pending: `points=None`,
  `multiplier=None`, `is_implemented=False`.
- Market amount/bonus keys: `points:order_item:{order_item_id}:{customer|seller}:{amount|bonus}`.

### Possible interpretations

| Option | Formula (per party) | Market 100.00 deal | Auction 100.00 deal | Notes |
| --- | --- | --- | --- | --- |
| A | `2 × floor(amount)` replaces the amount row | 200 + 100 bonus (+30 buyer badge PP) | 200 each (new) | Doubles the only symmetric deal reward |
| B | existing `floor(amount)` + an extra `floor(amount)` | same numbers as A | 200 each (new) | Same as A, but a second ledger row, which leaves old keys untouched |
| C | double every completion payout (amount, bonus, badge-paired, first-time) | buyer 2×(100+100+30) = 460, seller 400 (+100 first sale) | buyer 2×(50) + 2×200 first-time, seller 2×75 first sale | Doubles incentives that are not "shared" |
| D | `DEAL_COMPLETED.multiplier` (campaign) applied to the existing deal reward, default 1 | 100 + 100 unless a campaign sets 2 | unchanged unless auction gets a base | Matches §6.8 "double points" offer wording |

### Recommendation (not implemented)

Option B with configuration: a new shared completed-deal reward for **both**
deal types and **both** parties,

```text
deal_points = floor(CompletedDeal.amount) × DEAL_COMPLETED.multiplier   (default multiplier 2)
buyer  += deal_points
seller += deal_points
```

as one new row per party with key
`reward:completed_deal:{source_type}:{source_id}:{buyer|seller}`, replacing the
current market `int(amount)` row for deals completed after deployment only.
Keep the 100 PP market bonus, badge-paired, first-time, Lucky Wheel, and
aggregate rewards unchanged. Existing deals keep their historical rows; a new key
plus a pre-check for the legacy `points:order_item:{id}:{role}:amount` row
prevents any second payout when a historical deal's `DEAL_COMPLETED` is replayed.

### Decisions needed

1. **Base:** what does "double" apply to? Only the amount-based deal reward
   (A/B), all completion payouts (C), or is it a campaign multiplier (D)?
2. **Auction scope:** should auction deals get the same amount-based reward?
   Today they get none.
3. **Market +100 bonus:** keep, double, or remove?
4. **Rate and currency:** is it 1 PP per currency unit, and in which currency?
   Deals are stored in the seller's currency without conversion.
5. **Rounding:** keep `floor` (current), or round/ceil?
6. **Permanent rule or time-limited campaign** (§6.8 "seasonal double points")?

## 10. Successful Sale Reward (BE-REW-09)

Status: **DONE**

### Requirement

Every successfully completed sale (market and auction `CompletedDeal`) pays the
**seller only** a recurring reward, default **100 PP**, from a dedicated
`SUCCESSFUL_SALE` rule. Points only. Separate from the BE-REW-08 deal amount
reward and completion bonus, and from the one-time first-sale incentives.

### Pre-change findings

| Reward | Market seller | Auction seller | Recurring / one-time |
| --- | --- | --- | --- |
| Commercial deal amount reward (`DEAL_COMPLETED`, BE-REW-08) | `floor(amount × rate) × 2` | same | Recurring, every deal (buyer and seller) |
| Full-process completion bonus (`DEAL_COMPLETION_BONUS`, BE-REW-08) | 100 | 100 | Recurring, every deal (buyer and seller) |
| First-sale incentive + badge (`FIRST_MARKET_SALE_CONFIRMED` / `FIRST_AUCTION_SALE_CONFIRMED`) | 50 + badge | 75 + badge | One-time per seller profile and deal type; badge-paired |
| Seller-only recurring successful sale | none | none | **Not implemented** |

1. Keys in use: deal points `reward:completed_deal:{source_type}:{source_id}:{buyer|seller}`,
   bonus `reward:completed_deal_bonus:...`, first sale
   `reward:FIRST_*_SALE_CONFIRMED:{event}:{source}:profile:{seller}`.
2. Historical detection (BE-REW-08): `first_processing` is true only when the
   buyer's purchase-reward row for the deal does not exist yet at handler start.
   Shared deal rewards run only then.
3. No existing reward implements a recurring seller-only +100: the +100 bonus
   pays both parties, and the first-sale rewards pay once.

### Completed-deal reward concepts (after BE-REW-09)

| # | Concept | Rule | Who | When | Default |
| --- | --- | --- | --- | --- | --- |
| 1 | Commercial deal amount reward | `DEAL_COMPLETED` | buyer + seller | every completed deal | `floor(amount × rate) × multiplier` (rate EGP/USD 1, multiplier 2) |
| 2 | Full-process completion bonus | `DEAL_COMPLETION_BONUS` | buyer + seller | every completed deal | 100 PP each |
| 3 | Successful sale | `SUCCESSFUL_SALE` | **seller only** | every completed deal | 100 PP |
| 4 | First-sale incentive | constants `FIRST_*_SALE_POINTS` + badge | seller | first deal of each type only | market 50 PP, auction 75 PP |

These are separate rows with separate keys. Concepts 1 to 3 apply equally to
market and auction deals.

### Implemented behavior

```text
DEAL_COMPLETED -> RewardEngine._handle_deal_completed(deal)
   if first_processing:                       (BE-REW-08 detector, reused)
      |-- _grant_completed_deal_points  -> buyer + seller
      |-- _grant_completed_deal_bonus   -> buyer + seller
      '-- _grant_successful_sale_reward -> seller only   (BE-REW-09)
   purchase badge PP, first-sale badge PP, auction extras, aggregates (unchanged)
```

- Rule `SUCCESSFUL_SALE`: `enabled=True`, `points=100`, required `points`,
  `is_implemented=True`; read through `RewardRuleService`.
- Ledger row (seller): `user = deal.seller_user`, `profile = deal.seller_profile`,
  `points` = rule points, `reason = "Successful sale reward"`,
  `source_type = successful_sale`, `source_id = CompletedDeal.id`,
  `metadata = {"reward_code": "successful_sale", "deal_id", "deal_type"}`.
- Idempotency key: `reward:successful_sale:{deal.source_type}:{deal.source_id}:seller`
  (one per deal; different deals get different keys; the key is seller-scoped,
  so the reward can never be written for the buyer).
- Written through the shared `_grant_deal_reward_once` (key checked first,
  savepoint plus unique key for a concurrent duplicate). That helper now reuses
  `RewardEngine._paid_keys()` for the check (same query as before).
- Recurring: every newly processed deal pays it, independent of the first-sale
  badge or count.
- History and replay: only on the deal's first reward processing (BE-REW-08
  `first_processing`). Replaying an already-processed deal, including one
  processed before BE-REW-09 existed, pays nothing new. No backfill,
  compensation, or catch-up.
- Disabled rule: no successful-sale row; deal amount reward, bonus, badges,
  first-time rewards, Lucky Wheel, and trusted aggregates still run. A deal
  processed while the rule was disabled is not topped up after re-enabling.
- Config change: new deals use the new points; rewarded deals keep their rows.
- Independent of currency rates: a deal with an unconfigured currency (no deal
  points) still pays the successful-sale reward.
- Same-account deals pay the seller-role reward into the shared wallet, as
  before (not blocked).
- Points only; no badge, no special notification.

Example, 100 EGP deal with defaults (excluding the unchanged badge-paired and
first-time rows):

| Party | Row | PP |
| --- | --- | ---: |
| Buyer | `deal_completed` | 200 |
| Buyer | `deal_completion_bonus` | 100 |
| Seller | `deal_completed` | 200 |
| Seller | `deal_completion_bonus` | 100 |
| Seller | `successful_sale` | 100 |

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/loyalty/constants.py` | `RewardRuleCode.SUCCESSFUL_SALE` + default (100 PP, implemented); `RewardSourceType.SUCCESSFUL_SALE` | Dedicated rule |
| `apps/loyalty/reward_engine.py` | `_grant_successful_sale_reward`, called in the `first_processing` block; `_grant_deal_reward_once` uses `_paid_keys` | The reward |
| `apps/loyalty/migrations/0005_reward_rule_successful_sale.py` | Choices-only `AlterField` (`sqlmigrate`: `(no-op)` on PostgreSQL) | New rule code |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | 1 Arabic entry | Label |
| `tests/loyalty/test_successful_sale_reward.py` | New, 20 tests | BE-REW-09 coverage |
| `tests/loyalty/test_completed_deal_points.py`, `tests/loyalty/test_deal_completed_rewards.py` | Seller ledgers/balances include the new 100 PP row | Intended behavior change |
| `AGENTS.md`, `docs/rewards/rewarding-system-report.md` | Successful-sale reward described | No contradictions |

### Tests (`tests/loyalty/test_successful_sale_reward.py`)

Default rule; market and auction sales pay the seller 100 with the deal-source
key and nothing to the buyer; full 100 EGP market and auction ledgers per
party; three deals by one seller -> three rows, 300 PP, distinct keys; first
deal has separate successful-sale and first-sale rows, second deal repeats only
the successful sale; replay x3 pays once; an already-processed historical deal
gets nothing; config 100 -> 150 is not retroactive and applies to the next
deal; configured 150 on a new deal; disabled rule keeps deal points, bonus,
and badges; a deal processed while disabled is not topped up; a simulated
concurrent duplicate insert keeps one row and the balance; ledger attribution
(user, profile, reason, source, metadata); no badge added; runs without any
`PaymentRequest`; visible from the seller account in customer mode;
unconfigured currency still pays it; same-account deal pays the seller role.

Mutation checks: calling it outside the first-processing gate fails 2 tests;
making it first-sale-only fails 3; attributing it to the buyer fails 15. (A
second call for the buyer with the same seller-scoped key is already blocked by
the key.)

### Test results (2026-10-02, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/loyalty/test_successful_sale_reward.py -q` | 20 passed | 20 passed |
| `pytest tests/loyalty -q` | 7 failed / 192 passed | 199 passed |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| BE-REW-02..09 focused tests together | 151 passed | 151 passed |
| `pytest -q` | 47 failed / 951 passed | 10 failed / 988 passed |
| `makemigrations --check --dry-run` | No changes detected | |

No new failures: both full-suite failure sets are identical to the baseline at
commit `66d7992` (English-message assertions under the current Arabic test
settings; known chat, payments detail-permission, i18n cart, and migration
tests under `en`).

### Migration status

`apps/loyalty/migrations/0005_reward_rule_successful_sale.py`: choices-only
`AlterField` for the new `SUCCESSFUL_SALE` code; no table change
(`sqlmigrate` prints `(no-op)` on PostgreSQL). `0003` and `0004` untouched.

### Remaining risks

- **Farming.** Every completed deal now pays the seller 100 PP on top of 300+
  PP shared rewards, including tiny and same-account deals. No anti-farming
  rule exists.
- **Overlap with first-sale incentives.** A seller's first deal of each type
  pays both the successful sale (100) and the first-sale reward (50/75). This
  is intended for now; reconcile if product decides the first-sale rewards are
  obsolete.
- **Reward result classification (pre-existing, not fixed).** `BaseModel` uses
  django-extensions `TimeStampedModel`, so `created` and `modified` differ by
  microseconds on insert. `RewardEngine._grant_points`, `_grant_direct_points`,
  and `_award_badge` decide "new vs already awarded" with `created ==
  modified`, so newly created rows are reported under `already_awarded` in the
  `handle_event()` result. The ledger is correct; only the result dict
  misreports. The BE-REW-07/08/09 helpers append to `points_awarded`
  explicitly and are not affected.
- Tests run on SQLite, which does not enforce `select_for_update()` row locks.

## 11. Mutual Deal Rating Reward (BE-REW-10)

Status: **DONE**

### Requirement

When both parties of the same `CompletedDeal` have rated each other
(buyer -> seller and seller -> buyer), each receives `MUTUAL_RATING` points
(default 20 PP), once per deal. Points only.

### Pre-change findings (`apps/ratings`)

1. Ratings are created only by `Customer` profiles (`Rating.owner` is a
   `CustomerProfile`; `RatingValidator.get_customer_profile`).
2. Rated targets are an `Asset` (`AssetRating`) or a `SellerProfile`
   (`SellerRating`). Customers can never be rated; seller -> buyer is impossible.
3. Not deal-scoped: eligibility is "the customer has any completed `OrderItem`
   for this asset/seller". There is no link to a specific deal, auction deals are
   never eligible, and uniqueness is per (customer, asset) / (customer, seller)
   across all deals (partial unique constraints over non-deleted rows).
4. Conclusion: neither model can represent a deal-scoped mutual rating.
5. Rewards today: `create_asset_rating` / `create_seller_rating` emit
   `RATING_CREATED` -> 30 PP `rating_received` to the rated owner, plus
   `REVIEWER_BUYER` badge + 10 PP on the customer's first rating.
6. A new model that does not emit `RATING_CREATED` triggers none of these.
7. No rating update API; customers and sellers cannot delete ratings; managers
   soft-delete them (`ratings.delete`).
8. `RewardRuleCode.MUTUAL_RATING` exists (20 PP, `is_implemented=False`).

### Implemented behavior

```text
CompletedDeal
    |-- buyer  DealRating (buyer_to_seller) -> seller
    '-- seller DealRating (seller_to_buyer) -> buyer
              |
         both exist  (checked under the CompletedDeal row lock)
              |
    MUTUAL_RATING_COMPLETED   (emitted by DealRatingService, once per deal)
              |
    RewardEngine._handle_mutual_rating_completed
              |-- re-checks the pair exists
              |-- RewardRuleService(MUTUAL_RATING): enabled, points
              |-- buyer  += points   (default 20)
              '-- seller += points   (default 20)
```

Model (`apps/ratings/models.py`, `DealRating`, `BaseModel`):

| Field | Meaning |
| --- | --- |
| `deal` | FK `deals.CompletedDeal` (`PROTECT`, `related_name="ratings"`) |
| `direction` | `buyer_to_seller` or `seller_to_buyer` |
| `rater_profile` / `rated_profile` | FK `users.Profile` (`PROTECT`) |
| `stars` | 1-5 (same scale as `AssetRating` / `SellerRating`) |
| `comment` | optional text (same no-length-limit convention as rating comments) |

Constraints (migration `apps/ratings/migrations/0002_deal_rating.py`):

- `deal_ratings_unique_deal_rater`: unique `(deal, rater_profile)`
- `deal_ratings_unique_deal_direction`: unique `(deal, direction)`
- `deal_ratings_rater_not_rated`: `rater_profile != rated_profile`
- `deal_ratings_stars_1_to_5`
- The unique constraints are unconditional (they include soft-deleted rows), so a
  deleted rating can never be recreated.

Directions and eligibility:

- The rater is the caller's **active profile**. If it is `deal.buyer_profile`,
  the direction is `buyer_to_seller` and the rated profile is
  `deal.seller_profile`; if it is `deal.seller_profile`, the reverse. Any other
  profile gets 404. The caller never supplies the rated profile (extra fields
  are ignored).
- Only an existing `CompletedDeal` can be rated. Pending, rejected, or
  incomplete transactions have no deal, so they cannot be rated. No
  `PaymentRequest` query.
- One rating per party per deal; a second attempt returns 400, and the
  database constraint is the backstop. No update or delete API (unmapped
  methods are denied). Changing a rating through the ORM never re-emits the
  event, because emission happens only in `DealRatingService.create()`.

API (`apps/ratings/urls.py`, explicit routes, `HasAppAccess` resource
`DEAL_RATING`: customers and sellers `create` + `retrieve`, managers none):

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/api/deal-ratings/` | Body `deal_id`, `stars`, optional `comment`; returns the rating (201) |
| `GET` | `/api/deal-ratings/deals/<deal_id>/` | Participant only: `role`, `has_rated`, `counterparty_has_rated`, `is_mutual`, `my_rating`, `counterparty_rating` |

`counterparty_rating` is hidden until both parties have rated, so neither side
can retaliate against the other's rating. Non-participants get 404, and deal
ratings are not listed publicly.

Reward:

- Rule `MUTUAL_RATING` (existing since BE-REW-03; now `is_implemented=True`):
  `points=20`, enabled.
- Event `RewardEventType.MUTUAL_RATING_COMPLETED`, emitted synchronously inside
  the rating transaction when the saved rating completes the pair (either order).
- Ledger rows: key `reward:mutual_rating:deal:{deal_id}:{buyer|seller}`;
  `user`/`profile` = `deal.buyer_user`/`deal.buyer_profile` and
  `deal.seller_user`/`deal.seller_profile`; `source_type = mutual_rating`,
  `source_id = CompletedDeal.id` (never a rating id); reason
  `Mutual deal rating reward`; metadata `reward_code`, `deal_id`, `deal_type`.
- Written through `_grant_deal_reward_once` (paid-key check, savepoint, unique
  key), which reports new rows reliably and does not use the
  `created == modified` heuristic.
- Points only, no badge, no special notification.
- Disabled rule: ratings save normally and no PP is written. The pair can only
  be completed once, so no rating flow re-emits the event, and re-enabling the
  rule later does not top the deal up. A manual `handle_event` replay after
  re-enabling would pay once (keys still prevent duplicates); no code path does
  that.

Concurrency:

- `DealRatingService.create()` locks the `CompletedDeal` row with
  `select_for_update()` before checking and writing. Two near-simultaneous
  submissions for one deal are serialized: the second waits for the first to
  commit, then sees it and emits the event. That prevents both "neither sees
  the other" (missed reward) and duplicate rating rows.
- If two evaluations ever both emit, the per-party keys plus the savepoint
  keep one row per party.

Interaction with existing ratings:

- `AssetRating`, `SellerRating`, their APIs, eligibility, aggregates, and
  rewards are unchanged.
- `DealRating` never emits `RATING_CREATED`, so it does not pay the 30 PP
  `rating_received`, the `REVIEWER_BUYER` badge + 10 PP, or change
  `avg_rating` / `ratings_count`. A customer who also leaves a public
  `SellerRating` gets those existing rewards separately, as before.
- No backfill: existing `SellerRating` rows are not converted (they have no
  deal link), and no mutual rewards are paid for past deals.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/ratings/models.py` | `DealRatingDirection`, `DealRating` + constraints | Deal-scoped rating |
| `apps/ratings/services.py` | `DealRatingService` (`create`, `status`, `is_mutual`, deal lock, event emission) | Business flow |
| `apps/ratings/serializers.py` | `DealRatingCreateSerializer`, `DealRatingSerializer`, `DealRatingStatusSerializer` | API |
| `apps/ratings/views.py`, `apps/ratings/urls.py` | `DealRatingViewSet`, `DealRatingStatusViewSet`, two explicit routes | API |
| `apps/ratings/migrations/0002_deal_rating.py` | New table + 4 constraints | Schema |
| `apps/general/constants.py` | `DEAL_RATING` for customers and sellers (`create`, `retrieve`) | Endpoint gate |
| `apps/loyalty/constants.py` | `RewardEventType.MUTUAL_RATING_COMPLETED`, `RewardSourceType.MUTUAL_RATING`, `MUTUAL_RATING` implemented | Event + ledger source |
| `apps/loyalty/reward_engine.py` | `_handle_mutual_rating_completed` + dispatch | Reward |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | 4 Arabic entries | Messages |
| `tests/ratings/test_deal_ratings.py` | New, 31 tests | BE-REW-10 coverage |
| `AGENTS.md`, `docs/rewards/rewarding-system-report.md` | Deal rating API/behavior and reward | No contradictions |

No loyalty migration: the `MUTUAL_RATING` code already existed.

### Tests (`tests/ratings/test_deal_ratings.py`)

First rating saves without reward; reciprocal rating pays 20 each; reverse
order gives the same result; one-sided never pays; real market and auction
deals; replayed event pays once; two deals pay separately; strangers and
parties of another deal get 404; submitted `rated_profile_id` is ignored;
non-existent deal 404; stars 0/6 rejected; manager 403; duplicate rejected
(400) and blocked by the database; ORM edit and denied PATCH do not pay
again; a soft-deleted rating cannot be recreated; points 35; disabled rule
saves ratings, pays nothing, and is not topped up; no badge; ledger
user/profile; key/source/metadata reference the deal; shared wallet in another
mode; public `SellerRating` still pays 30 PP + `REVIEWER_BUYER`; deal ratings
pay no public rating rewards and create no `SellerRating`; double emission
with stale reads keeps one row per party and balances; a stray event without
both ratings pays nothing; service-level create completes the pair;
same-account deal completes and pays both roles; status hides the counterparty
rating until mutual; status is private to participants.

Mutation checks: removing the handler's pair re-check fails 1 test; removing
the service emission fails 14; removing the service duplicate check fails
none because the database constraint still blocks recreation (intended
backstop).

### Test results (2026-10-02, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/ratings/test_deal_ratings.py -q` | 31 passed | 31 passed |
| `pytest tests/ratings -q` | 10 failed / 103 passed | 113 passed |
| `pytest tests/loyalty -q` | 7 failed / 192 passed | 199 passed |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| BE-REW-02..10 focused tests together | 182 passed | 182 passed |
| `pytest -q` | 47 failed / 982 passed | 10 failed / 1019 passed |
| `makemigrations --check --dry-run` | No changes detected | |

No new failures: both full-suite failure sets are identical to a fresh baseline
taken before BE-REW-10 (the `tests/ratings` and `tests/loyalty` failures under
current settings are English-message assertions under the Arabic test
settings).

### Migration status

`apps/ratings/migrations/0002_deal_rating.py` (new table `ratings_dealrating`
with the four constraints above). No loyalty migration.

### Remaining risks

- **Self-dealing.** A same-account deal can be rated in both directions by
  switching profiles, paying both 20 PP rewards into one wallet. Not blocked.
- **Rating farming.** Every completed deal (including tiny ones) can now yield
  40 PP of mutual rating rewards with no quality check on the rating content.
- **Retaliation window.** Counterparty ratings are hidden until mutual, but
  there is no deadline; a party can withhold their rating indefinitely and the
  other side never sees theirs.
- **No moderation.** `DealRating` has no manager read/delete API; comments are
  not filtered for contact sharing (consistent with existing ratings).
- **Existing technical debt (not fixed).** The older `RewardEngine` helpers
  (`_grant_points`, `_grant_direct_points`, `_award_badge`) classify results by
  `created == modified`, which is never true for django-extensions timestamps,
  so new rows from those helpers appear under `already_awarded`. The new mutual
  rating path does not use that pattern.
- Tests run on SQLite, which ignores `select_for_update()`; the concurrent
  second-rating path must be verified on PostgreSQL.

## 12. Admin-Approved Referrals (BE-REF-01)

Status: **DONE**

### Requirement

Referred members must be accepted manually by administration (anti-bot).
Redeeming a code creates a pending referral and pays nothing; only a manager
approval makes it reward-eligible, and then the inviter gets the
`REFERRAL_APPROVED` reward (default 30 PP).

### Pre-change findings (`apps/referrals`)

1. `LinkSharingCode` (owner_profile, code, expires_at, is_used,
   used_by_profile, used_at). `ReferralService.create_code()` makes a 12-char
   `uuid4().hex` code (retry on collision) for the active profile.
2. Single-use: `redeem_code` locks the code row and rejects `is_used`.
3. TTL `REFERRAL_CODE_TTL_HOURS = 24`.
4. New-account window `REFERRAL_NEW_ACCOUNT_WINDOW_HOURS = 2`
   (`now - user.date_joined`).
5. Self-referral blocked by owner profile id and by same `UserAccount`.
6. **Multiple claims are not prevented:** a new account can redeem several
   different codes within its 2-hour window, and every inviter is paid.
7. Reward timing: on redemption, immediately, `REFERRAL_INVITER_POINTS = 30`
   hard-coded, `source_type=referral_accepted`, key
   `points:referral:{code_id}:inviter:{owner_profile_id}`, to the code owner's
   account and profile.
8. `successful_invites_count` (`apps/users/serializers.py`, exposed by the
   seller/customer admin and mobile profile serializers) counts
   `LinkSharingCode` rows with `is_used=True` per owner profile.
9. No other subsystem reads `is_used`. Only
   `tests/rewards_sprint` covers redemption.
10. `RewardRuleCode.REFERRAL_APPROVED` exists (30 PP, `is_implemented=False`).

### Implemented behavior

```text
LinkSharingCode (share token, single use; is_used = consumed)
      |
   redeem  (new account, 2 h window; 24 h code TTL; not own account)
      |
Referral(PENDING)   -- 0 PP
      |
 manager review (referrals.approve / referrals.reject)
    /          \
APPROVED      REJECTED  -- 0 PP, final
   |
REFERRAL_APPROVED event
   |
RewardEngine._handle_referral_approved
   |-- RewardRuleService(REFERRAL_APPROVED)  (default 30 PP)
   '-- PointService -> inviter account only
```

Model (`apps/referrals/models.py`, `Referral`, `BaseModel`):

| Field | Meaning |
| --- | --- |
| `code` | OneToOne `LinkSharingCode` (`PROTECT`): one referral per code |
| `inviter_user` / `inviter_profile` | Code owner's account and profile |
| `referred_user` / `referred_profile` | Redeeming account and its active profile |
| `status` | `pending` (default), `approved`, `rejected` |
| `redeemed_at` | Redemption time |
| `reviewed_at`, `reviewed_by` | Review time and reviewer `UserAccount` (`SET_NULL`) |
| `rejection_reason` | Optional text |

Constraints (migration `apps/referrals/migrations/0002_referral.py`):

- `referrals_unique_referred_user`: unique `referred_user` over **all** rows, so
  one account can be referred once, ever (account-level identity; other
  profiles of the account cannot claim again; rejection does not allow
  referral shopping).
- `referrals_not_self`: `inviter_user != referred_user`.
- OneToOne `code`: one referral per code.
- Indexes `(inviter_user, status)` and `(status, redeemed_at)` for approved
  counts and the review queue.

Redemption (`ReferralService.redeem_code`, `POST /api/referrals/redeem/`):

- Unchanged checks: code required, account at most 2 hours old, code exists
  (row locked), not already used, not expired, not the redeemer's own account
  or profile.
- New check: the account has no referral yet (any status); the unique
  constraint is the backstop (`IntegrityError` -> 400).
- Marks the code consumed (`is_used`, `used_by_profile`, `used_at`) and creates
  a `pending` referral. **No PP.**
- Response: the previous code fields plus `referral`
  `{id, status, referred_profile_id, redeemed_at}`. The response is additive;
  existing fields are kept.
- `GET /api/referrals/my/`: the caller's invited referrals with status
  (`status` filter); no reviewer data.

Admin review (`HasAppAccess`, resource `REFERRAL_ADMIN`, exact manager codes):

| Method | Path | Permission | Behavior |
| --- | --- | --- | --- |
| `GET` | `/api/referrals/admin/` | `referrals.list` | Filters `status`, `inviter_user`, `referred_user` |
| `POST` | `/api/referrals/admin/<id>/approve/` | `referrals.approve` | `pending -> approved`; already approved returns unchanged (no second event); rejected -> 400 |
| `POST` | `/api/referrals/admin/<id>/reject/` | `referrals.reject` | `pending -> rejected` with optional `reason`; already rejected returns unchanged; approved -> 400 |

Customers and sellers get 403. Both transitions lock the referral row
(`select_for_update`) inside `transaction.atomic()`.

Reward:

- Approval emits `RewardEventType.REFERRAL_APPROVED` synchronously inside the
  approval transaction (same convention as the other reward events: if the
  reward write fails, the approval rolls back too).
- `_handle_referral_approved` pays only approved referrals: the inviter
  receives `REFERRAL_APPROVED.points` (default 30, `is_implemented=True`), key
  `reward:referral_approved:referral:{referral_id}:inviter`,
  `user`/`profile` = `inviter_user`/`inviter_profile`,
  `source_type = referral_approved`, `source_id = Referral.id`, reason
  `Referral approved reward`, metadata `reward_code`, `referral_id`,
  `referred_user_id`. Written through `_grant_points_once` (paid-key check,
  savepoint, unique key).
- The referred user receives nothing. There is no badge, notification,
  Lucky Wheel attempt, first-bid reward, or challenge entry.
- Disabled rule: approval still succeeds and nothing is paid. Re-approving is a
  no-op, so enabling the rule later does not top up.
- The hard-coded `REFERRAL_INVITER_POINTS = 30` was removed.

Successful invites:

- `successful_invites_count` (seller/customer admin and mobile profile
  serializers) now counts `approved` referrals of the inviter profile
  (`ReferralService.approved_count`). Pending, rejected, and merely consumed
  codes do not count. The per-profile scope is unchanged.

Legacy data (migration `apps/referrals/migrations/0003_backfill_legacy_referrals.py`):

- Before BE-REF-01 redemption meant accepted and paid (30 PP,
  `source_type=referral_accepted`, key
  `points:referral:{code_id}:inviter:{owner_profile_id}`).
- The data migration turns each used, non-deleted code with a redeemer into an
  `approved` referral (`reviewed_by` empty, `redeemed_at`/`reviewed_at` =
  `used_at`), earliest first. A second code redeemed by an account that already
  has a referral (possible before BE-REF-01) is skipped and stays consumed only;
  codes whose redeemer profile is gone are skipped. It is reward-silent: no
  events and no ledger rows. It is safe to re-run, and its reverse is a no-op.
- Why legacy referrals cannot be paid again: backfilled rows are already
  `approved`, so the approve endpoint is a no-op; and `_handle_referral_approved`
  treats an existing legacy key for the referral's code as already paid, even
  if the event is replayed.
- Effect: existing `successful_invites_count` values are preserved, and future
  BE-REF-02/03 lookups (referred account -> approved referral -> inviter) work
  for legacy referrals too.

Not implemented in BE-REF-01: the first-bid inviter reward
(`REFERRAL_FIRST_BID`, added by BE-REF-02, section 13), the monthly
challenge / leaderboard / prizes (added by BE-REF-03, section 14), referral
notifications.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/referrals/models.py` | `ReferralStatus`, `Referral` + constraints | Verified relationship separate from code consumption |
| `apps/referrals/services.py` | Redeem creates `pending` (no PP, one per account); `approve`, `reject`, `approved_count`; `REFERRAL_INVITER_POINTS` removed | Admin approval flow |
| `apps/referrals/serializers.py`, `views.py`, `urls.py` | Redeem response with referral status; `my/`, `admin/`, `admin/<id>/approve/`, `admin/<id>/reject/` | User and manager API |
| `apps/referrals/migrations/0002_referral.py` | New table + constraints | Schema |
| `apps/referrals/migrations/0003_backfill_legacy_referrals.py` | Reward-silent backfill of legacy used codes | Preserve counts and lookups |
| `apps/loyalty/constants.py` | `RewardEventType.REFERRAL_APPROVED`, `RewardSourceType.REFERRAL_APPROVED`, rule implemented | Event + ledger source |
| `apps/loyalty/reward_engine.py` | `_handle_referral_approved`; generic `_grant_points_once` (the deal helper now delegates to it, same behavior) | Reward |
| `apps/users/serializers.py` | `successful_invites_count` counts approved referrals | Correct statistic |
| `apps/users/manager_permissions.py` | `referrals.list`, `referrals.approve`, `referrals.reject` | Exact manager permissions |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | 11 Arabic entries | Messages and permission labels |
| `tests/referrals/test_admin_approved_referrals.py` | New, 31 tests | BE-REF-01 coverage |
| `tests/rewards_sprint/test_rewards_sprint.py` | Redeem test now expects a pending referral and no PP | Intended behavior change |
| `AGENTS.md`, `docs/rewards/rewarding-system-report.md` | Referral API, flow, permissions | No doc says redemption pays |

### Tests (`tests/referrals/test_admin_approved_referrals.py`)

Pending redemption with 0 PP and correct parties; self-referral (same profile
and other mode) rejected; expired code; 2-hour window unchanged; single-use
code; one referral per account; another mode of the same account cannot claim;
database rejects a second referral for an account; rejection blocks referral
shopping; approval pays 30 and records the reviewer; rejection pays nothing;
customers and sellers cannot list/approve/reject; managers need exact codes;
repeated approval pays once; transitions only from pending; unknown referral
404; admin list filters by status; `my/` shows status without review data;
replayed event pays once; points 50; disabled rule approves, pays nothing, and
is not topped up; ledger attribution; simulated concurrent duplicate insert
keeps one row; event for a pending referral pays nothing; approval plus a bid by
the referred member creates no challenge rewards (since BE-REF-02 the bid also
pays `REFERRAL_FIRST_BID`, section 13);
`successful_invites_count` counts approved only; legacy codes backfill as
approved without paying again (double-claim and orphan skipped); backfill is
idempotent; blank code rejected; backfill runs with historical migration
models.

Mutation checks: paying (approving) at redemption fails 10 tests; removing the
legacy guard fails 1; counting consumed codes fails 1; removing the service
one-per-account check fails none because the database constraint still blocks
it (intended backstop).

Migration verification: test settings disable migrations
(`MIGRATION_MODULES = DisableMigrations()`), so all migrations were run with
`migrate` on a scratch SQLite database with migrations enabled: every app
migrated from scratch; then `referrals` was rolled back to `0001`, legacy data
was seeded (two codes redeemed by the same account, one orphan used code, one
unused code), and migrating forward created exactly one `approved` referral for
the earliest code with zero ledger rows. Rolling back and re-applying gave the
same result.

### Test results (2026-10-02, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/referrals -q` | 31 passed | 31 passed |
| `pytest tests/loyalty -q` | 7 failed / 192 passed | 199 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/users -q` | 1 failed / 45 passed | 46 passed |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| `pytest tests/ratings -q` | 10 failed / 103 passed | 113 passed |
| BE-REW-02..10 + BE-REF-01 focused tests together | 213 passed | 213 passed |
| `pytest -q` | 47 failed / 1013 passed | 10 failed / 1050 passed |
| `makemigrations --check --dry-run` | No changes detected | |

No new failures: both full-suite failure sets are identical to a fresh baseline
taken before BE-REF-01 (baseline: 47 failed / 982 passed and 10 failed / 1019
passed; the 31 new referral tests account for the difference). Current-settings
failures in `tests/loyalty`, `tests/users`, and `tests/ratings` are known
English-message assertions under the Arabic test settings.

### Migration status

- `apps/referrals/migrations/0002_referral.py`: creates `referrals_referral`
  with the constraints above.
- `apps/referrals/migrations/0003_backfill_legacy_referrals.py`: data
  migration, reward-silent, reverse no-op.
- No loyalty migration (`REFERRAL_APPROVED` code already existed).

### Remaining risks

- **Review backlog.** Inviters are paid only after a manager acts; pending
  referrals have no expiry or SLA.
- **Account-level inviter vs per-profile count.** Referral identity is
  account-level, but `successful_invites_count` stays per inviter profile (its
  existing scope); an account with codes from two modes sees split counts.
- **Legacy double claims.** Accounts that redeemed several codes before
  BE-REF-01 paid several inviters; only the earliest becomes the referral. The
  extra inviters keep their old 30 PP (no clawback).
- **Bot resistance relies on review.** No device, IP, or rate limit checks
  were added; the new-account window and manual approval are the controls.
- **Legacy reward detection uses the old ledger key.** If legacy rows were
  edited outside the ledger, a backfilled referral would still not pay,
  because the approve endpoint is a no-op for approved rows.
- Tests run on SQLite, which ignores `select_for_update()`; concurrent
  approvals should be verified on PostgreSQL (the unique reward key is the
  backstop).
- **Existing technical debt (not fixed).** Older `RewardEngine` helpers
  misreport new rows as `already_awarded` in event results (see section 10).

## 13. Referral First-Bid Reward (BE-REF-02)

Status: **DONE**

### Requirement verification (decision gate, 2026-10-02)

- Section 5 rule catalog (BE-REW-03, committed in `66d7992`):
  `REFERRAL_FIRST_BID | 200 PP | points | No | Confirmed value; reward not
  implemented`. The BE-REW-03 product brief listed `REFERRAL_FIRST_BID =
  200 PP` among the confirmed rule values.
- Code default: `DEFAULT_REWARD_RULES[REFERRAL_FIRST_BID]` is `points=200`,
  `implemented=False`.
- Section 5 risk note: `REFERRAL_FIRST_BID` (referral) is not the existing
  `first_bid_completed` 200 PP buyer reward.
- Section 12 and `AGENTS.md`: `REFERRAL_FIRST_BID` is the referred member's
  first-bid **inviter** reward, deferred to BE-REF-02; section 12 makes only
  `approved` referrals reward-eligible.
- `docs/rewards/rewarding-system-report.md` does not mention the rule.
- Not defined anywhere: whether approval must precede the first bid. The
  non-retroactive default is used (see below).

Result: 200 PP is confirmed; implementation proceeds.

### Pre-change findings

1. A regular bid is persisted only in `BiddingService.place_bid`
   (`apps/auctions/services.py`): it locks the `AuctionItem`, runs
   `BidBusinessValidator` (customer profile, active window, active item,
   amount rules), then `Bid.objects.create(...)`. A failed validation raises
   before any row is saved, so rejected attempts leave no `Bid`.
2. `BID_PLACED` is emitted once per saved bid from
   `BiddingService._handle_bid_reward`, in the same transaction, after the
   `bid_placed` `AuctionEvent`. No other code emits `BID_PLACED`.
3. `Bid` uses safedelete `SOFT_DELETE` (`Bid.objects` hides deleted rows,
   `Bid.all_objects` includes them). No API deletes bids.
4. Buy-now (`SettlementService.trigger_buy_now`) creates a `Bid` row and a
   `buy_now_triggered` `AuctionEvent` that points to it, but does not emit
   `BID_PLACED`.
5. Existing first-bid logic: `FIRST_BIDDER` badge + 10 PP to the bidder when
   its **profile** has no other non-deleted bid. It is per profile and ignores
   soft-deleted bids. Unchanged.
6. The existing 200 PP `first_bid_completed` reward is paid to the **buyer**
   account once, on its first completed auction deal (key
   `points:first_bid_completed:user:{user_id}`). It is a purchase reward, not
   this referral reward. Unchanged.
7. `REFERRAL_FIRST_BID` already existed as a `RewardRuleCode` (200 PP,
   `enabled=True`, `implemented=False`); no `RewardRuleConfig` row by default.
8. A user has at most one profile per type (`unique_together (user, type)`),
   and only customer profiles can place bids through the service.

### Implemented behavior

```text
Referral(APPROVED, reviewed_at <= bid time)
      |
referred account places a regular bid (BiddingService.place_bid)
      |
BID_PLACED -> RewardEngine._handle_bid_placed
      |-- FIRST_BIDDER / last-minute rewards (unchanged)
      |-- ACTIVE_AUCTION_BIDDER (unchanged, BE-REW-07)
      '-- _grant_referral_first_bid_reward
              |-- account's earliest bid ever?  no -> stop
              |-- RewardRuleService(REFERRAL_FIRST_BID)  (default 200 PP)
              '-- PointService -> inviter account
```

Qualification (all must hold at evaluation time):

- The bid arrived through `BID_PLACED`, i.e. a valid regular bid. Buy-now never
  pays this reward.
- The bidder's `UserAccount` is a `Referral.referred_user` with status
  `approved`. Pending, rejected, deleted, and missing referrals do not pay.
  `LinkSharingCode.is_used` is not consulted.
- The referral was approved before the bid was placed
  (`referral.reviewed_at <= bid.created`). Backfilled legacy referrals have
  `reviewed_at` = their old redemption time, so they qualify.
- The bid is the account's **earliest bid ever**: no other `Bid` row of any
  profile of the account, on any auction or item, created earlier (ties broken
  by id). The query uses `Bid.all_objects`, so soft-deleted bids count, and
  `bidder__user_id`, so seller-mode or other-profile rows count. Bid rows with a
  `buy_now_triggered` event are excluded from this history: buy-now neither
  pays nor uses up the milestone.
- The rule is enabled with points > 0.

Reward (`RewardEngine._grant_referral_first_bid_reward`, through
`_grant_points_once`):

| Field | Value |
| --- | --- |
| `user` / `profile` | `referral.inviter_user` / `referral.inviter_profile` (shared account wallet; the context profile is the one that issued the code, whatever mode the inviter is in) |
| `points` | `REFERRAL_FIRST_BID.points` (default 200) |
| `source_type` / `source_id` | `referral_first_bid` / `Referral.id` |
| `idempotency_key` | `reward:referral_first_bid:referral:{referral_id}:inviter` |
| `reason` | `Referral first bid reward` |
| `metadata` | `reward_code`, `referral_id`, `referred_user_id` |

The referred bidder receives nothing from this rule. No badge, Lucky Wheel
attempt, notification, or challenge entry. `successful_invites_count` is
unchanged (it counts approved referrals).

Not retroactive (no catch-up, no backfill):

- A first bid placed while the referral was pending pays nothing, and a later
  approval does not look back at bids; the next bid is not a first bid. The
  `reviewed_at` check also keeps a replayed event for that bid from paying.
- A first bid placed while the rule was disabled pays nothing, and enabling
  the rule later does not pay later bids.
- Accounts whose bids predate BE-REF-02 (including legacy referred accounts
  with old bids) never pay: their next bid is not their first.
- A legacy approved referral whose account has never bid pays on its first
  new bid.

Idempotency and concurrency:

- One reward per referral: the key is referral-level, not `Bid.id`. Deleting
  the rewarded bid cannot create another milestone, and replays pay once.
- The payload depends only on the referral, so a stale evaluation that misses
  the paid check gets an identical-payload replay from `PointService` (no
  error that would break the bid); a concurrent duplicate insert hits the
  unique key inside a savepoint and is a no-op.
- Two concurrent first bids from one account on different items hold
  different item locks; both may see themselves as first, and the unique key
  keeps one inviter row.

Independence: one bid can trigger `FIRST_BIDDER` for the bidder,
`ACTIVE_AUCTION_BIDDER` for the auction's bidders, and `REFERRAL_FIRST_BID`
for the inviter; each has its own eligibility and keys. The buyer's 200 PP
`first_bid_completed` purchase reward is separate and unchanged.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/loyalty/reward_engine.py` | `_grant_referral_first_bid_reward`, called from `_handle_bid_placed` | Reward evaluation on `BID_PLACED` |
| `apps/loyalty/constants.py` | `RewardSourceType.REFERRAL_FIRST_BID`; `REFERRAL_FIRST_BID` rule `implemented=True` | Ledger source; rule is now read |
| `tests/referrals/test_referral_first_bid_reward.py` | New, 32 tests | BE-REF-02 coverage |
| `tests/referrals/test_admin_approved_referrals.py` | `test_approval_and_first_bid_create_no_challenge_rewards` now expects the first-bid reward too | Intended behavior change (BE-REF-01 asserted it did not exist yet) |
| `AGENTS.md` | Rule list, source constants, referral behavior | Current state |

No translation change: the label "Referral First Bid" already had an Arabic
entry (rule label).

### Tests (`tests/referrals/test_referral_first_bid_reward.py`)

Approved first bid pays 200; the referred bidder earns the same as a
non-referred bidder; pending, rejected, and no referral pay nothing; approval
before the bid pays; second bid, another auction, and another item of the same
auction pay nothing more; a historical bid, a soft-deleted historical bid, and a
bid from the account's seller profile block the milestone; deleting the
rewarded bid cannot be farmed; mode switching cannot create a second
milestone; a rejected bid attempt does not use the milestone; buy-now neither
pays nor uses the milestone; approval after the first bid is not retroactive
(including a replayed event); points 350; disabled rule skips and is not
topped up; rule marked implemented; replayed event pays once; ledger
attribution and key; stale concurrent insert keeps one row; stale paid check
replays the same payload; separate referrals pay their own inviters; reward
lands in the shared wallet while the inviter is in seller mode;
`FIRST_BIDDER` unchanged; `ACTIVE_AUCTION_BIDDER` unchanged; the buyer's
`first_bid_completed` 200 PP stays separate; legacy approved referral with old
bids pays nothing; legacy approved referral without bids pays on its first
new bid; full lifecycle (redeem 0, approve 30, first bid 200).

Mutation checks: removing the approval-timing check, using `Bid.objects`
(hides deleted rows), counting buy-now rows as history, checking history per
profile, accepting any referral status, adding the bid id to the payload, and
using a bid-based key each fail at least one test; removing the first-bid
check fails 6; not wiring the handler fails 19.

### Test results (2026-10-02, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/referrals/test_referral_first_bid_reward.py -q` | 32 passed | 32 passed |
| `pytest tests/referrals -q` | 63 passed | 63 passed |
| `pytest tests/auctions -q` | 9 failed / 71 passed | 80 passed |
| `pytest tests/loyalty -q` | 7 failed / 192 passed | 199 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/users -q` | 1 failed / 45 passed | 46 passed |
| BE-REW-02..10 + BE-REF-01/02 focused tests together | 245 passed | 245 passed |
| `pytest -q` | 47 failed / 1045 passed | 10 failed / 1082 passed |
| `makemigrations --check --dry-run` | No changes detected | |

No new failures: both full-suite failure sets are identical to a fresh baseline
taken before BE-REF-02 (47 failed / 1013 passed and 10 failed / 1050 passed;
the 32 new tests account for the difference). Current-settings failures in
`tests/auctions`, `tests/loyalty`, and `tests/users` are known English-message
assertions under the Arabic test settings.

### Migration status

None. `REFERRAL_FIRST_BID` was already a `RewardRuleCode` choice;
`RewardSourceType` is not a model field choice list.

### Remaining risks

- **Manual replay after enabling a disabled rule.** If the rule was disabled
  at the first bid and someone later replays that bid's `BID_PLACED` by hand,
  it would pay (the bid is still the earliest). Normal flows never replay.
- **Buy-now history relies on its event row.** A buy-now `Bid` is recognized
  by its `buy_now_triggered` `AuctionEvent`; if that event row were
  hard-deleted, the buy-now bid would count as history and block the
  milestone.
- **Hard-deleted bids are not history.** Soft deletes are covered; a bid
  removed with a hard delete (for example a cascading hard delete of its
  auction) would no longer block a later "first" bid.
- Tests run on SQLite; concurrent first bids on PostgreSQL rely on the unique
  key (verified here with simulated stale reads, not real parallel
  transactions).

## 14. Monthly Referral Challenge (BE-REF-03)

Status: **DONE**

### Requirement

Monthly competition of referral activity; referred members are accepted
manually by administration (anti-bot); Top 10 leaderboard; countdown;
personal progress; prizes: rank 1 iPhone, rank 2 AirPods, rank 3 annual Loft
subscription.

### Pre-change findings

1. `Referral` (BE-REF-01) has `inviter_user` / `inviter_profile`,
   `referred_user` / `referred_profile`, `status`
   (`pending`/`approved`/`rejected`), `redeemed_at` (always set), and
   `reviewed_at` / `reviewed_by`. Approval locks the referral row.
2. `LinkSharingCode.is_used` only means a code was consumed. There is no
   share, click, or view tracking anywhere.
3. Existing `Referral` indexes: `(inviter_user, status)` and
   `(status, redeemed_at)`.
4. Backfilled legacy referrals are `approved` with `redeemed_at` = their old
   redemption time.
5. `successful_invites_count` counts approved referrals **per inviter
   profile** (unchanged; see risks).
6. An abandoned, untracked BE-REF-03 attempt was found in the working tree
   before this work: a migration
   `0004_referralchallenge_referralchallengeprize_and_more.py` for models that
   did not exist (`makemigrations --check` failed because of it), an empty
   `tests/referrals/test_referral_challenge.py`, and 8 compiled strings in
   `django.mo` without `.po` entries. They were backed up outside the
   repository and replaced by this implementation.

### Flow

```text
Referral redeemed in [starts_at, ends_at)
        |
     PENDING ---- manager review (also allowed during REVIEW)
        |                \
     APPROVED          REJECTED (never scores)
        |
  +1 challenge score for referral.inviter_user   (not PP)
        |
  account-level Top 10 + personal progress + countdown
        |
  ends_at reached -> REVIEW (new redemptions fall outside the window)
        |
  every in-window referral resolved
        |
  FINALIZE (manager) -> immutable Top 3 ReferralChallengeWinner snapshots
```

### Scoring

```text
challenge_score(account) = COUNT(Referral
    WHERE status = 'approved'
      AND inviter_user = account
      AND challenge.starts_at <= redeemed_at < challenge.ends_at)
```

- Only approved referrals count. Raw shares, clicks, codes
  (`LinkSharingCode.is_used`), pending, rejected, and soft-deleted referrals
  never count. Approval is the anti-bot boundary.
- Grouped by `Referral.inviter_user`: codes shared from the customer and the
  seller mode of one account add up to one score. Not grouped by profile.
- `redeemed_at` decides which challenge owns a referral; approval only
  decides whether it counts. A referral redeemed at Jan 31 23:30 and approved
  on Feb 2 counts for January. `reviewed_at` is never used.
- Half-open interval everywhere (scoring, pending guard, review backlog):
  `starts_at <= redeemed_at < ends_at`.
- Challenge score is not PP: scoring and finalization create no
  `PointTransaction`. The `REFERRAL_APPROVED` (+30 PP) and
  `REFERRAL_FIRST_BID` (+200 PP) rewards are independent; a referral can
  earn its inviter PP and +1 score at the same time. First bids, deals, and
  payments do not affect the score.
- Legacy approved referrals count when their `redeemed_at` is in a window.

### Domain (`apps/referrals/models.py`)

| Model | Fields | Rules |
| --- | --- | --- |
| `ReferralChallenge` | `name`, `starts_at`, `ends_at`, `finalized_at`, `created_by`, `finalized_by` (`UserAccount`, `SET_NULL`) | DB check `starts_at < ends_at`; periods cannot overlap (service) |
| `ReferralChallengePrize` | `challenge`, `rank` (1-3), `prize_code` (`IPHONE`, `AIRPODS`, `ANNUAL_LOFT_SUBSCRIPTION`), `title`, `metadata` | Unique live `(challenge, rank)`; rank between 1 and 3 |
| `ReferralChallengeWinner` | `challenge`, `rank`, `user`, `score`, `score_achieved_at`, `prize`, `prize_code`, `prize_title` | Unique `(challenge, rank)` and `(challenge, user)`; rank 1-3; score >= 1; the model refuses updates and deletes |

Lifecycle (derived by `ReferralChallenge.status_at(now)`, no scheduled job,
no stored status):

| Status | Condition |
| --- | --- |
| `draft` | `now < starts_at` |
| `active` | `starts_at <= now < ends_at` |
| `review` | `now >= ends_at` and `finalized_at` is null |
| `finalized` | `finalized_at` is set |

Dates are explicit timezone-aware boundaries chosen by the manager (for
example Oct 1 00:00 to Nov 1 00:00); nothing assumes 30 days or a calendar
month string.

Overlap: a new or edited period is rejected when
`new.starts_at < existing.ends_at AND new.ends_at > existing.starts_at` for
any live challenge; adjacent periods (one ends exactly when the next starts)
are allowed. Writes first lock all challenge rows with `select_for_update()`
so concurrent writes are serialized and the overlap check sees committed rows.

### Leaderboard and progress (`ReferralChallengeService`)

- One aggregate query: approved in-window referrals grouped by
  `inviter_user`, with `COUNT` (score) and `MAX(redeemed_at)` (the moment the
  account reached its final score), ordered by:
  1. higher score;
  2. earlier `MAX(redeemed_at)` (who reached the score first);
  3. lower `UserAccount` id.

  This tie-break is a technical policy; the product specification does not
  define ties.
- Top 10 only; accounts with score 0 never appear and empty places are not
  filled.
- Personal progress for the requesting account (any mode):
  `challenge_score`, `rank` (null at 0, otherwise the same position the
  leaderboard uses), `leader_score`, `gap_to_first` (`0` for the leader).
  Structured data only; the frontend renders text such as
  "Your score: 15 | 5 behind first".
- Countdown, computed per request from one `now`: `server_time`,
  `seconds_remaining` (`max(ends_at - now, 0)` while active, otherwise 0),
  `seconds_until_start` (`max(starts_at - now, 0)`). Nothing ticking is
  stored.
- Indexes: no new index. Scoring and the pending check filter on
  `(status, redeemed_at)` (existing `ref_status_redeemed_idx`); personal
  progress filters on `(inviter_user, status)` (existing
  `ref_inviter_status_idx`). The challenge table holds one row per month, so
  its date lookups need no index.

### Editing

| Status | Allowed changes |
| --- | --- |
| `draft` | `name`, `starts_at`, `ends_at`, `prizes` (supplied `prizes` replace the whole set; old rows are soft-deleted) |
| `active`, `review` | `name` only; sending `starts_at`, `ends_at`, or `prizes` is rejected |
| `finalized` | nothing |

### Finalization (`POST .../finalize/`)

In one transaction, after locking the challenge row:

1. already finalized: return the existing result (idempotent, no new rows);
2. `now < ends_at`: rejected;
3. any `pending` referral with `starts_at <= redeemed_at < ends_at`: rejected
   with "Challenge has pending referrals awaiting review." Pending referrals
   outside the window do not block;
4. take the Top 3 of the final leaderboard (fewer when fewer accounts score;
   no zero-score winners);
5. write one `ReferralChallengeWinner` per rank with the score, the
   tie-break time, and the configured prize of that rank (FK plus copied
   code and title);
6. set `finalized_at` and `finalized_by`.

Winner rows are never recalculated: later referral or prize changes do not
alter them. Concurrent finalizations are serialized by the challenge lock;
the second returns the first result. An approval or rejection still in
progress leaves its referral `pending` in committed data, so the pending check
refuses finalization; one that already committed is simply part of the
ranking. Once the check passes, the in-window result cannot change through the
services: no in-window referral is pending (approved and rejected are final)
and new redemptions fall after `ends_at`. Challenge edits and finalization
lock the same challenge row.

### Prizes

- Created with the default Top 3 unless the manager supplies prizes:
  1 `IPHONE` "iPhone", 2 `AIRPODS` "AirPods", 3 `ANNUAL_LOFT_SUBSCRIPTION`
  "Annual Loft subscription". Generic definitions only: no model, capacity,
  generation, value, or Loft package SKU. `metadata` is free manager data
  (admin API only).
- **Fulfillment is not implemented**: no shipping, address or identity
  collection, subscription activation, cash, PP, or delivery workflow. The
  winner snapshot only records who won which prize. Future flow:
  `ReferralChallengeWinner -> PrizeAward -> digital fulfillment / physical
  claim` (not built).
- No PP is paid for any rank; no notifications are sent.

### API

| Method | Path | Access | Behavior |
| --- | --- | --- | --- |
| `GET` | `/api/referrals/challenge/current/` | Authenticated | Active challenge, else nearest upcoming one; 404 when neither (a review or finalized challenge is never "current"). Returns `id`, `name`, `status`, `starts_at`, `ends_at`, countdown fields, `top_10` (`rank`, `username`, `challenge_score`, `is_me`), `me` (progress), `prizes` (`rank`, `prize_code`, `title`) |
| `GET` | `/api/referrals/admin/challenges/` | `referral_challenges.list` | Challenges with status, prizes, winners |
| `POST` | `/api/referrals/admin/challenges/` | `referral_challenges.create` | `name`, `starts_at`, `ends_at`, optional `prizes`; records `created_by` |
| `GET` | `/api/referrals/admin/challenges/<id>/` | `referral_challenges.retrieve` | Adds the live `leaderboard` (with `user_id`; `null` once finalized) and `pending_referrals_count` |
| `PATCH` | `/api/referrals/admin/challenges/<id>/` | `referral_challenges.update` | Editing rules above |
| `POST` | `/api/referrals/admin/challenges/<id>/finalize/` | `referral_challenges.finalize` | Finalization above |

- Admin views use `HasAppAccess` with resource `REFERRAL_CHALLENGE_ADMIN` and
  exact manager codes; customers and sellers get 403.
- Public rows expose only `username` (the account's public handle, already
  public as the seller nickname); no account id, email, phone, national id,
  or prize metadata.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/referrals/models.py` | `ReferralChallengeStatus`, `ReferralChallengePrizeCode`, `ReferralChallenge`, `ReferralChallengePrize`, `ReferralChallengeWinner` | Challenge domain |
| `apps/referrals/services.py` | `ReferralChallengeService` (scoring, leaderboard, progress, countdown, current, create, update, finalize) | Business rules |
| `apps/referrals/serializers.py` | Public current-challenge serializers; admin list/detail/write/winner serializers | Separate public and admin views |
| `apps/referrals/views.py`, `urls.py` | `CurrentReferralChallengeViewSet`, `AdminReferralChallengeViewSet`, `AdminReferralChallengeFinalizeViewSet`; 4 routes | API |
| `apps/referrals/migrations/0004_referral_challenge.py` | New tables and constraints | Schema |
| `apps/users/manager_permissions.py` | `referral_challenges.list/retrieve/create/update/finalize` | Exact manager permissions |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | 26 Arabic entries | Messages, labels, permissions |
| `tests/referrals/test_referral_challenge.py` | New, 51 tests | Coverage |
| `AGENTS.md`, `docs/rewards/rewarding-system-report.md` | Referral challenge state | Current state |

### Tests (`tests/referrals/test_referral_challenge.py`)

Approved in-window referral scores 1; pending and rejected do not; consumed
codes do not; customer and seller modes of an inviter combine into one row;
the window is half-open (before excluded, start included, end excluded);
approval during review counts for the redemption window; approval inside a
window does not pull in a referral redeemed outside it; a backfilled legacy
referral counts by redemption time; Top 10 only; zero-score accounts are
absent; ties go to the earlier achiever (who has the higher id), then to the
lower id; progress 15 vs 20 gives rank 2 and gap 5; leader gap 0; progress
rank matches the leaderboard under ties; zero-score progress has no rank;
derived statuses; countdown per status; the current endpoint returns
countdown, Top 10, progress, and prizes (frozen time); public rows expose
only `rank`, `username`, `challenge_score`, `is_me`; the current endpoint
falls back to the next upcoming challenge and never returns a review or
finalized one; the same score in every mode; overlap rejected (service and
API); adjacent periods allowed; invalid period rejected (API and DB
constraint); default prizes and creator recorded; prize ranks unique, 1-3,
and known codes; draft dates and prizes editable (old prizes soft-deleted);
draft edit cannot overlap; started challenge dates and prizes frozen but name
editable; finalized challenge read-only; active challenge cannot finalize;
in-window pending referral blocks finalization (exact message); pending
outside the window does not block; finalization after reviews snapshots the
Top 3 with IPHONE / AIRPODS / ANNUAL_LOFT_SUBSCRIPTION; fewer entrants give
fewer winners; no entrants give no winners; finalizing twice keeps one result;
winner snapshots ignore later referral and prize changes and refuse updates
and deletes; scoring and finalization create no PP; approval still pays +30
PP and adds +1 score; the first-bid +200 PP reward is independent of the
score; customers and sellers cannot use any admin endpoint; managers need the
exact code for each action; admin detail shows the live leaderboard and the
pending backlog; admin list shows status and prizes; the referral migrations
match the models (autodetector, untranslated) and include the expected
constraints.

Mutation checks (each fails at least one test): closed window end, grouping
by profile, counting pending referrals, no achievement-time tie-break, no
pending guard, finalizing before the end, non-idempotent finalization, dates
editable after start, window by `reviewed_at`, no overlap check, Top 12, and
returning a past challenge as current.

### Migration verification

`ppw/settings/test.py` disables migrations, so the real path was checked
separately with scratch settings (`MIGRATION_MODULES = {}`, SQLite file):

- `migrate` from an empty database applied every app's migrations, including
  `referrals.0004_referral_challenge`;
- the tables carry `ref_chal_starts_before_ends`, `ref_chal_prize_rank_range`,
  `ref_chal_winner_rank_uniq`, `ref_chal_winner_user_uniq`,
  `ref_chal_winner_rank_range`, `ref_chal_winner_score_gte_1`, and the partial
  unique index `ref_chal_prize_rank_uniq` (`WHERE deleted IS NULL`);
- rolling `referrals` back to `0003` removed the three tables, and
  re-applying `0004` restored them;
- `pytest tests/referrals` on a test database built by running the
  migrations: 114 passed.

### Test results (2026-10-03, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/referrals/test_referral_challenge.py -q` | 51 passed | 51 passed |
| `pytest tests/referrals -q` | 114 passed | 114 passed |
| `pytest tests/loyalty -q` | 7 failed / 192 passed | 199 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/users -q` | 1 failed / 45 passed | 46 passed |
| `pytest tests/auctions -q` | 9 failed / 71 passed | 80 passed |
| `pytest tests/test_api_docs.py -q` | 6 passed | 6 passed |
| BE-REW-02..10 + BE-REF-01/02/03 focused tests together | 296 passed | 296 passed |
| `pytest -q` | 47 failed / 1096 passed | 10 failed / 1133 passed |
| `makemigrations --check --dry-run` | No changes detected | |
| `spectacular --validate` | Valid (no warnings from the new code) | |

No new failures: both full-suite failure sets are identical to a fresh baseline
taken before BE-REF-03 (47 failed / 1045 passed and 10 failed / 1082 passed;
the 51 new tests account for the difference). Current-settings failures in
`tests/loyalty`, `tests/users`, and `tests/auctions` are known English-message
assertions under the Arabic test settings.

### Remaining risks

- **First challenge creation race.** The overlap check is serialized by
  locking existing challenge rows; when no challenge exists yet, two
  simultaneous creations could both pass. Only managers create challenges.
- **Profile-level `successful_invites_count`.** The profile serializers still
  count approved referrals per inviter profile (BE-REF-01), while the
  challenge counts per account. An account that shares from both modes sees
  split counts there but one combined challenge score.
- **Past start dates are allowed.** A manager can create a challenge whose
  window already started or ended; it then includes referrals already
  redeemed in that window. This is intentional flexibility (for example
  creating October's challenge on Oct 1 at 10:00) and is manager-only.
- **Review backlog has no deadline.** A review challenge stays unfinalized
  until every in-window referral is approved or rejected.
- **Prize fulfillment is out of scope** (see Prizes).
- Tests run on SQLite, which ignores `select_for_update()`; concurrent
  finalization and overlap serialization should be verified on PostgreSQL.

## 15. Seller Season Scoring (BE-SEA-01)

Status: **DONE**

### Requirement

Score and rank participating sellers for a season window `[starts_at, ends_at)`
using the finalized weighted formula:

$$\text{season\_score} = \text{sales\_score} \times 0.40 + \text{rating\_score} \times 0.30 + \text{reliability\_score} \times 0.20 + \text{interaction\_score} \times 0.10$$

All four component scores are normalized to `0..100` using `Decimal`, and the
composite `season_score` is quantized to `Decimal("0.01")` with
`ROUND_HALF_UP`. Season score calculation is pure and side-effect-free with
respect to rewards and notifications.

### Pre-change findings

1. `Season` (`apps/seasons/models.py`) defines `title`, `starts_at`, `ends_at`,
   `status` (`draft`, `calculated`, `finalized`), `calculated_at`,
   `finalized_by`, and `metadata`.
2. `SeasonSellerInsight` previously stored only `completed_orders_count`,
   `avg_rating` (copied from lifetime `SellerProfile.avg_rating`),
   `total_sales_amount`, `rank`, `points_awarded`, and `metadata`.
3. `SeasonService.calculate_winners` previously:
   - counted only market `OrderItem(status=COMPLETED)` rows (ignoring auction
     sales and `CompletedDeal`),
   - required `MINIMUM_COMPLETED_ORDERS = 10`,
   - ranked by `(-completed_orders_count, -avg_rating, -total_sales_amount, id)`,
   - awarded `100,000 / 50,000 / 25,000` PP (`RewardSourceType.SEASON_WINNER`)
     during `calculate_winners`, before season finalization.
4. `AuctionService.cancel_auction` updated `AuctionItem.status` via queryset
   `.update(status=CANCELLED)` without updating `modified` and hardcoded
   `performer=auction.owner`.

### Finalized scoring specification

- **Participant identity:** `SellerProfile` (`SeasonSellerInsight.seller`
  remains a `ForeignKey` to `SellerProfile`, unique per `(season, seller)`).
- **Season window:** Half-open interval
  `season.starts_at <= event_time < season.ends_at`.
- **Eligibility:** A `SellerProfile` participates in a season if and only if
  it has at least **1 `CompletedDeal`** in `[season.starts_at, season.ends_at)`.
  Sellers with `0` completed deals in the season do not get a
  `SeasonSellerInsight` row (any stale row is removed on recalculation). The
  legacy `MINIMUM_COMPLETED_ORDERS = 10` threshold is removed.
- **Component 1 — Completed Sales (`40%`):**
  - Source: non-deleted `CompletedDeal` rows (`MARKET` and `AUCTION`) where
    `seller_profile = seller` and
    `season.starts_at <= completed_at < season.ends_at`.
  - Raw metric: `completed_sales_count`.
  - Normalization: let `season_max_completed_sales` be the maximum
    `completed_sales_count` across qualifying sellers in the season. If
    `season_max_completed_sales == 0`, `sales_score = 0`; otherwise:
    $$\text{sales\_score} = \frac{\text{completed\_sales\_count}}{\text{season\_max\_completed\_sales}} \times 100$$
- **Component 2 — Buyer Ratings (`30%`):**
  - Source: non-deleted `DealRating` rows with
    `direction = DealRatingDirection.BUYER_TO_SELLER`, `rated_profile = seller`,
    `season.starts_at <= deal.completed_at < season.ends_at`, and
    `season.starts_at <= rating.created < season.ends_at`.
  - Legacy `SellerRating`, `AssetRating`, `SellerProfile.avg_rating`, and
    `DealRatingDirection.SELLER_TO_BUYER` are ignored.
  - Raw metrics: `ratings_count`, `avg_rating` (`0.00` when `ratings_count == 0`).
  - Normalization: if `ratings_count == 0`, `rating_score = 0`; otherwise:
    $$\text{rating\_score} = \frac{\text{avg\_rating}}{5} \times 100$$
- **Component 3 — Seller Reliability (`20%`):**
  - Counts seller-responsible failures at business-unit granularity
    (`OrderItem` for market, `AuctionItem` for auction), excluding any unit that
    produced a `CompletedDeal`:
    1. Market seller rejection: `OrderItem.status = REJECTED` with
       `season.starts_at <= order_item.modified < season.ends_at`.
    2. Market seller-timeout auto-cancellation: `OrderItem.status = CANCELLED`
       with an `OrderEvent(event_type=SELLER_AUTO_CANCELLED)` whose `created`
       is in `[season.starts_at, season.ends_at)`.
    3. Seller/owner auction cancellation: `AuctionItem.status = CANCELLED` on
       an `Auction(status=CANCELLED)` where the cancellation `AuctionEvent` in
       `[season.starts_at, season.ends_at)` was performed by `auction.owner`
       (and not marked as admin/manager/buyer/customer cancellation). Each
       cancelled `AuctionItem` counts as 1 unit.
  - Excludes buyer cancellations, admin/manager cancellations or closes, and
    `PaymentRequest` rejections.
  - Formula: `reliability_opportunities = completed_sales_count + seller_cancellations_count`.
    If `reliability_opportunities == 0`, `reliability_score = 0`; otherwise:
    $$\text{reliability\_score} = \frac{\text{completed\_sales\_count}}{\text{reliability\_opportunities}} \times 100$$
- **Component 4 — Buyer Interaction (`10%`):**
  - Verified-buyer reply rate across distinct buyer `UserAccount`s who have at
    least 1 `CompletedDeal` with this `seller` in
    `[season.starts_at, season.ends_at)`:
    - `interaction_opportunities_count`: distinct verified buyer `UserAccount`s
      who sent at least 1 direct `Message` (`sender = customer_profile`,
      `receiver = seller_profile`) in `[season.starts_at, season.ends_at)`.
    - `interaction_replied_count`: distinct qualifying buyers who received at
      least 1 direct `Message` (`sender = seller_profile`,
      `receiver = customer_profile`) in `[season.starts_at, season.ends_at)`
      created **after** that buyer's first qualifying in-season buyer$\to$seller
      message.
  - Multiple messages from the same buyer count as 1 opportunity; multiple
    replies to the same buyer count as 1 replied buyer; seller-initiated
    messages without a prior in-season buyer message do not count.
  - Formula: if `interaction_opportunities_count == 0`,
    `interaction_score = 0`; otherwise:
    $$\text{interaction\_score} = \frac{\text{interaction\_replied\_count}}{\text{interaction\_opportunities\_count}} \times 100$$
- **Ranking & tie-break:**
  1. `season_score DESC`
  2. `SellerProfile.id ASC`
- **Pure calculation invariant:** `SeasonScoreService.calculate_season_scores`,
  `SeasonService.calculate_winners`, `SeasonService.calculate_scores`,
  `SeasonService.recalculate`, and `GET /api/seasons/<id>/seller-insights/`
  create **0** `PointTransaction` rows, **0** badges, **0** prizes/trophies,
  and **0** notifications. `points_awarded` is persisted as `0` for new/updated
  insight snapshots, while historical `PointTransaction` ledger rows remain
  untouched.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/seasons/models.py` | Added component counts/scores and `season_score` to `SeasonSellerInsight`; updated `Meta.ordering` to `["rank", "-season_score", "seller_id"]` | Persist full seller scoring breakdown |
| `apps/seasons/migrations/0002_seller_season_scoring.py` | Schema migration for `SeasonSellerInsight` scoring fields and ordering | Database schema |
| `apps/seasons/services.py` | Added `SeasonScoreService` and updated `SeasonService` (`calculate_winners`, `calculate_scores`, `recalculate`, `insights`); removed premature PP payout during score calculation | Pure weighted seller season scoring |
| `apps/seasons/serializers.py`, `apps/seasons/views.py` | Exposed all component metrics and `season_score` on `SeasonSellerInsightSerializer` | API contract |
| `apps/auctions/services.py`, `apps/auctions/serializers.py` | `AuctionService.cancel_auction` accepts optional `performer` and updates `AuctionItem.modified`; `CancelAuctionSerializer` passes `performer=request.user.active_profile` | Distinguish seller vs manager auction cancellations |
| `tests/seasons/test_seller_season_scoring.py` | New, 43 tests (`test_01` through `test_43`) | Full BE-SEA-01 coverage and BE-REF/BE-REW regressions |
| `AGENTS.md`, `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md` | Updated seasons documentation and task status | Keep docs aligned with code |

### Test results (2026-10-04, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/seasons -q` | 47 passed | 47 passed |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| `pytest tests/ratings -q` | 10 failed / 103 passed | 113 passed |
| `pytest tests/loyalty -q` | 7 failed / 192 passed | 199 passed |
| `pytest tests/referrals -q` | 114 passed | 114 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/auctions -q` | 9 failed / 71 passed | 80 passed |
| BE-REW-02..10 + BE-REF-01..03 + BE-SEA-01 focused tests | 351 passed | 351 passed |
| `makemigrations --check --dry-run` | No changes detected | |

## 16. Season Finalization and Winner Payouts (BE-SEA-02)

Status: **DONE**

### Requirement & Canonical Payout Confirmation

Season rewards must be paid **only** when a season is finalized after its end
time, and each season winner must be paid at most once.

Canonical documentation explicitly confirms the fixed Top-3 winner PP Coin
payouts:

- `AGENTS.md` (`apps.seasons` section): `top 3 rewards are 100000, 50000, and 25000 PP with source season_winner`
- `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md` (Section 4 — BE-REW-03 & Section 15 — BE-SEA-01): `Season winner PP {1: 100000, 2: 50000, 3: 25000}` (`RewardSourceType.SEASON_WINNER`)

| Final Rank | PP Coin Payout |
| --- | ---: |
| Rank 1 | `100,000` PP |
| Rank 2 | `50,000` PP |
| Rank 3 | `25,000` PP |
| Rank 4+ | `0` PP |

If only 1 seller qualifies in the season, only Rank 1 is paid; if 2 qualify,
only Ranks 1 and 2 are paid; if 0 qualify, finalization completes with `0`
payouts and marks the season `FINALIZED`.

### Architecture & Finalization Flow

```text
SeasonScoreService
      ↓
recalculable leaderboard (0 PP)
      ↓
season ends (timezone.now() >= season.ends_at)
      ↓
manager finalize (SeasonService.finalize)
      ↓
one final score refresh (SeasonScoreService.calculate_season_scores)
      ↓
freeze insights/ranks
      ↓
SeasonPayoutService.payout_season_winners
      ↓
Top 3 PP once (PointService.earn_points)
      ↓
FINALIZED (status, finalized_by, finalized_at)
```

- **`SeasonScoreService` (`apps/seasons/services.py`):** Pure scoring and
  ranking service. Creates `0` PP, `0` badges, `0` prizes/trophies, and `0`
  notifications. Rejects recalculation once `season.status == SeasonStatus.FINALIZED`.
- **`SeasonPayoutService` (`apps/seasons/services.py`):** Winner PP payout
  service called during finalization. Credits `seller.user`'s shared
  `PointWallet` via `PointService.earn_points` (`user = seller.user`,
  `profile = seller`, `source_type = RewardSourceType.SEASON_WINNER`,
  `source_id = str(season.pk)`).
- **`SeasonService.finalize(season, manager_profile=None)`:** Runs inside a
  single `transaction.atomic()` block and locks `Season` with
  `select_for_update()`:
  1. If `season.status == SeasonStatus.FINALIZED`, returns `season` unchanged
     (idempotent retry: no recalculation, no rank modification, `finalized_at`
     and `finalized_by` preserved, no duplicate PP).
  2. Verifies `timezone.now() >= season.ends_at`; raises `ValidationError`
     (`"Season cannot be finalized before it ends."`) if still active.
  3. Runs `SeasonScoreService.calculate_season_scores(season)` one final time
     from current source data so finalization never pays a stale leaderboard.
  4. Reloads final ranked `SeasonSellerInsight` rows.
  5. Calls `SeasonPayoutService.payout_season_winners(season, insights=insights)`
     to pay eligible Top-3 sellers once and update `points_awarded`.
  6. Marks `season.status = SeasonStatus.FINALIZED`, records
     `season.finalized_by = manager_profile`, and sets `season.finalized_at`
     (stored in `season.metadata["finalized_at"]` and exposed via
     `Season.finalized_at` property and `SeasonSerializer` without requiring a
     schema migration).

### Idempotency & Legacy Rank-Key Compatibility

- **Stable logical identity:** `reward:season_winner:season:{season_id}:seller:{seller_id}`.
  Rank is stored in `PointTransaction.metadata` (`season_id`, `seller_id`,
  `rank`, `season_score`), **not** in `idempotency_key`. A seller cannot be paid
  twice for the same season even if their rank changed before finalization.
- **Legacy safety:** Before BE-SEA-01, `calculate_winners` created
  `SEASON_WINNER` transactions with rank-based keys
  `season:{season_id}:seller:{seller_id}:rank:{rank}`.
  `SeasonPayoutService.get_historical_winner_transactions(season, seller)`
  detects any non-deleted `SEASON_WINNER` transaction for the same `(season, seller)`
  (matching by `source_id`, canonical key, legacy `season:{id}:seller:{id}:*`
  prefix, or `metadata.season_id`).
- **No historical top-up / no clawback:** If a seller already has any historical
  `SEASON_WINNER` transaction in that season (even at a different rank/amount,
  e.g. paid `50,000` PP at rank 2 earlier and finishing at rank 1), BE-SEA-02
  treats that seller as already paid for the season: it does **not** top up to
  `100,000` PP, does **not** add another `100,000` PP, and does **not** claw
  back or rewrite the ledger.
- **Multiple historical payouts:** If old code paid the same seller multiple
  times due to rank changes across recalculations, BE-SEA-02 creates no
  additional payout, leaves historical ledger rows untouched, and leaves
  `insight.points_awarded = 0` rather than inventing a reconciliation value.
- **`points_awarded` behavior:**
  - New payouts: Rank 1 $\to$ `100000`, Rank 2 $\to$ `50000`, Rank 3 $\to$ `25000`, Rank 4+ $\to$ `0`.
  - Unambiguous single historical payout: `insight.points_awarded` reflects that
    single transaction's `points`.
  - Multiple historical payouts: `insight.points_awarded` remains `0` (the
    `PointTransaction` ledger remains the financial source of truth).

### Atomicity, Concurrency, and Finalized Immutability

- **All-or-nothing atomicity:** Finalization runs in one outer
  `transaction.atomic()`. If any winner payout fails mid-loop (e.g. Rank 2
  raises after Rank 1 succeeded), the entire transaction rolls back: Rank 1's
  ledger row is removed, Rank 1's wallet balance is restored, and `season`
  remains unfinalized so finalization can be retried cleanly.
- **Concurrency:** Concurrent finalization requests serialize on
  `Season.objects.select_for_update().get(pk=season.pk)`, with
  `PointTransaction`'s unique `idempotency_key` constraint as the database
  backstop.
- **Finalized immutability:** Once `season.status == SeasonStatus.FINALIZED`:
  - `SeasonService.calculate_winners`, `SeasonService.calculate_scores`,
    `SeasonService.recalculate`, `SeasonScoreService.calculate_season_scores`,
    and `POST /api/seasons/<id>/calculate-winners/` reject recalculation with
    `ValidationError` (`"Finalized seasons cannot be recalculated."`).
  - `GET /api/seasons/<id>/seller-insights/` returns stored frozen
    `SeasonSellerInsight` rows without recalculating.
  - `starts_at` and `ends_at` cannot be modified via `SeasonSerializer`
    (`PATCH /api/seasons/<id>/`) or `Season.save()` (`"Finalized season dates cannot be modified."`).
- **Separation from BE-SEA-03:** BE-SEA-02 implements only the final ranking
  freeze and fixed one-time PP payouts. Configurable season prizes, trophies,
  `PrizeAward`, physical fulfillment, and winner notifications are out of scope.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/seasons/models.py` | Added `Season.finalized_at` property/setter backed by `metadata["finalized_at"]`; enforced `starts_at`/`ends_at` immutability on finalized seasons in `Season.clean()` | Record finalization timestamp and protect finalized season window without schema migration |
| `apps/seasons/services.py` | Added `SEASON_WINNER_POINTS`, `SeasonPayoutService`, `SeasonService.finalize`, and finalized-season guard in `SeasonScoreService.calculate_season_scores` | Final score refresh, rank-independent idempotent winner payouts, legacy safety, and finalized immutability |
| `apps/seasons/serializers.py` | Exposed read-only `finalized_at` and rejected `starts_at`/`ends_at` edits on finalized seasons | API contract and date immutability |
| `apps/seasons/views.py` | Delegated `SeasonFinalizeViewSet.create` to `SeasonService.finalize` | Orchestrate finalization through service layer |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | Added Arabic translations for season finalization validation errors | Localization |
| `tests/seasons/test_season_finalization_payouts.py` | New, 39 tests (`test_01` through `test_39`) | Full BE-SEA-02 coverage |
| `tests/seasons/test_seasons.py`, `tests/test_i18n.py` | Updated legacy finalize stub assertions to match BE-SEA-02 finalization rules | Keep existing season and i18n suites aligned |
| `AGENTS.md`, `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md`, `docs/rewards/rewarding-system-report.md` | Documented BE-SEA-02 architecture, payout rules, legacy safety, and test results | Keep canonical docs aligned with code |

### Test results (2026-10-04, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/seasons/test_season_finalization_payouts.py -q` | 39 passed | 39 passed |
| `pytest tests/seasons -q` | 86 passed | 86 passed |
| `pytest tests/loyalty -q` | 7 failed / 192 passed | 199 passed |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| `pytest tests/ratings -q` | 10 failed / 103 passed | 113 passed |
| `pytest tests/referrals -q` | 114 passed | 114 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/test_i18n.py -q` | 7 passed | 2 failed / 5 passed (expected Arabic-default assertions) |
| BE-REW-02..10 + BE-REF-01..03 + BE-SEA-01..02 focused tests | 390 passed | 390 passed |
| `makemigrations --check --dry-run` | No changes detected | |

## 17. Admin-Configurable Season Prizes (BE-SEA-03)

Status: **DONE**

### Requirement & Symbolic Prize Decision Gate

Replace the hard-coded season payout schedule (`SEASON_WINNER_POINTS = {1: 100000, 2: 50000, 3: 25000}`) as the runtime payout source with per-season admin-configurable `SeasonPrize` definitions while preserving all BE-SEA-01 scoring and BE-SEA-02 finalization, idempotency, atomicity, and legacy-safety guarantees.

- **Canonical Defaults:** When a new `Season` is created (via API, `SeasonService.create_season`, or `Season.objects.create`), 3 default `SeasonPrize` rows are automatically created:
  - `rank=1`: `pp_points=100000`
  - `rank=2`: `pp_points=50000`
  - `rank=3`: `pp_points=25000`
- **Symbolic / Trophy Decision Gate:** Canonical documentation (`AGENTS.md`, `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md`, `docs/rewards/rewarding-system-report.md`) does not define mandatory symbolic trophy codes (`GOLD` / `SILVER` / `BRONZE`) or fulfillment workflows for seasons. Accordingly, `SeasonPrize` supports optional `symbolic_prize_code`, `title`, and `metadata` fields for descriptive display and winner snapshots, while only `pp_points` executes automatically via `PointService.earn_points`. No badge creation, `PrizeAward`, physical/cash fulfillment, or notification side effects are triggered.

### Domain Model (`apps/seasons/models.py`)

`SeasonPrize(BaseModel)`:

| Field | Type / Rules |
| --- | --- |
| `season` | `ForeignKey(Season, on_delete=CASCADE, related_name="prizes")` |
| `rank` | `PositiveSmallIntegerField`, validators & DB check `1 <= rank <= 3` (`season_prize_rank_range`) |
| `pp_points` | `PositiveIntegerField(default=0)`, validators & DB check `pp_points >= 0` (`season_prize_pp_points_gte_0`) |
| `symbolic_prize_code` | `CharField(max_length=64, blank=True, default="")` |
| `title` | `CharField(max_length=255, blank=True, default="")` |
| `metadata` | `JSONField(default=dict, blank=True)` |
| `created_by` | `ForeignKey("users.ManagerProfile", on_delete=SET_NULL, null=True, blank=True)` |
| `modified_by` | `ForeignKey("users.ManagerProfile", on_delete=SET_NULL, null=True, blank=True)` |

- Partial unique constraint `season_prize_rank_uniq` on `("season", "rank")` where `deleted__isnull=True`.
- Ordering: `["rank", "id"]`.

### Editability & Freeze Rules (`SeasonPrizeService` & `SeasonPrize`)

- **Editable window (`DRAFT` before `starts_at`):**
  - Allowed only when `season.status == SeasonStatus.DRAFT` **and** `timezone.now() < season.starts_at`.
  - Authorized managers may list, create missing Rank `1..3` configs, update `rank` / `pp_points` / `symbolic_prize_code` / `title` / `metadata`, or delete a rank config before the season starts.
- **Frozen once started, calculated, or finalized:**
  - If `timezone.now() >= season.starts_at`: rejected with `"Season prizes cannot be modified after the season starts."`
  - If `season.status == SeasonStatus.CALCULATED`: rejected with `"Season prizes can only be modified while the season is in draft."`
  - If `season.status == SeasonStatus.FINALIZED`: rejected with `"Finalized season prizes cannot be modified."`
  - Updating a `DRAFT` season so `starts_at <= timezone.now()` immediately blocks any subsequent prize mutations.

### Finalization & Payout Integration (`SeasonPayoutService` & `SeasonService.finalize`)

1. **Pre-payout completeness check:** Before paying any winner, `SeasonPayoutService.payout_season_winners` inspects the qualifying Top-3 insights (`insight.rank in (1, 2, 3)`). Every qualifying winning rank `1..N` (`N <= 3`) must have an active `SeasonPrize` row. If any qualifying rank is missing its `SeasonPrize`, `ValidationError` (`"Season prize configuration is incomplete."`) is raised **before** any payout executes (`0` new `PointTransaction` rows, season remains unfinalized). If fewer than 3 sellers qualify (e.g. only Ranks 1 and 2), a missing Rank 3 prize does not block finalization.
2. **Winner prize snapshot:** On finalization, each Top-3 winner insight (`rank in (1, 2, 3)`) stores `insight.metadata["season_prize"] = {"rank": prize.rank, "pp_points": prize.pp_points, "symbolic_prize_code": prize.symbolic_prize_code, "title": prize.title}` so historical season results are self-contained.
3. **Zero-point prizes (`pp_points == 0`):** Valid configuration. The winner receives no `PointTransaction`, `insight.points_awarded = 0`, and `insight.metadata["season_prize"]` is still recorded.
4. **Positive-point prizes (`pp_points > 0`):** Paid once to the shared `PointWallet` of `seller.user` with `profile=seller`, `source_type=RewardSourceType.SEASON_WINNER`, `source_id=str(season.pk)`, and the rank- and amount-independent idempotency key `reward:season_winner:season:{season_id}:seller:{seller_id}`.
5. **Legacy safety:** `SeasonPayoutService.get_historical_winner_transactions(season, seller)` runs before any new payout. If a historical `SEASON_WINNER` transaction exists for `(season, seller)`, no new transaction is created, no top-up is issued if `SeasonPrize.pp_points` is higher, and no clawback occurs if `SeasonPrize.pp_points` is lower.

### Migration & Existing Season Safety (`apps/seasons/migrations/0003_season_prize.py`)

- Creates `seasons_seasonprize` with `season_prize_rank_uniq`, `season_prize_rank_range`, and `season_prize_pp_points_gte_0`.
- `backfill_non_finalized_season_prizes` backfills default `100,000 / 50,000 / 25,000` PP `SeasonPrize` rows for existing non-finalized seasons (`status != "finalized"` and `deleted__isnull=True`).
- Existing `FINALIZED` seasons are not backfilled, not recalculated, and not re-paid (`0` `PointTransaction` rows created).

### Admin API & Permissions

| Method | Path | Permission | Behavior |
| --- | --- | --- | --- |
| `GET` | `/api/seasons/<season_pk>/prizes/` | `seasons.prizes.list` | List configured `SeasonPrize` rows for the season |
| `POST` | `/api/seasons/<season_pk>/prizes/` | `seasons.prizes.create` | Create a missing Rank `1..3` prize while `DRAFT` before `starts_at` |
| `PATCH` | `/api/seasons/<season_pk>/prizes/<id>/` | `seasons.prizes.update` | Update a prize while `DRAFT` before `starts_at` |
| `DELETE` | `/api/seasons/<season_pk>/prizes/<id>/` | `seasons.prizes.delete` | Soft-delete a prize while `DRAFT` before `starts_at` |

`SeasonSerializer` also exposes read-only `prizes` (`id`, `rank`, `pp_points`, `symbolic_prize_code`, `title`).

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/seasons/models.py` | Added `DEFAULT_SEASON_PRIZE_POINTS`, `Season.ensure_default_prizes()`, and `SeasonPrize` with constraints and editability guards | Per-season Rank 1..3 prize domain model and automatic defaults |
| `apps/seasons/migrations/0003_season_prize.py` | Created `SeasonPrize` table/constraints and non-finalized season default prize backfill | Schema and data migration |
| `apps/seasons/services.py` | Added `SeasonPrizeService`; updated `SeasonPayoutService` and `SeasonService` to validate/pay configured `SeasonPrize` rows and snapshot `metadata["season_prize"]` | Configurable payouts, pre-start freeze enforcement, and winner prize snapshots |
| `apps/seasons/serializers.py` | Added `SeasonPrizeSummarySerializer` and `SeasonPrizeSerializer`; exposed read-only `prizes` on `SeasonSerializer` | API contract and payload validation |
| `apps/seasons/views.py`, `apps/seasons/urls.py` | Added `SeasonPrizeViewSet` and `/api/seasons/<season_pk>/prizes/` routes | Manager CRUD endpoints |
| `apps/users/manager_permissions.py` | Registered `seasons.prizes.list`, `seasons.prizes.create`, `seasons.prizes.update`, `seasons.prizes.delete` | Exact manager action permissions |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | Added Arabic translations for season prize errors and permission labels | Localization |
| `tests/seasons/test_season_prizes.py` | New, 41 tests (`test_01` through `test_41`) | Full BE-SEA-03 coverage |
| `AGENTS.md`, `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md`, `docs/rewards/rewarding-system-report.md` | Documented `SeasonPrize` architecture, freeze rules, payout rules, and permissions | Keep canonical docs aligned with code |

### Test results (2026-10-05, `ppw-env`)

| Command | Current settings | `LANGUAGE_CODE="en"` |
| --- | --- | --- |
| `pytest tests/seasons/test_season_prizes.py -q` | 41 passed | 41 passed |
| `pytest tests/seasons/test_season_finalization_payouts.py -q` | 39 passed | 39 passed |
| `pytest tests/seasons -q` | 127 passed | 127 passed |
| `pytest tests/loyalty -q` | 7 failed / 192 passed | 199 passed |
| `pytest tests/deals -q` | 17 passed | 17 passed |
| `pytest tests/ratings -q` | 10 failed / 103 passed | 113 passed |
| `pytest tests/referrals -q` | 114 passed | 114 passed |
| `pytest tests/rewards_sprint -q` | 8 passed | 8 passed |
| `pytest tests/test_i18n.py -q` | 7 passed | 2 failed / 5 passed (expected Arabic-default assertions) |
| BE-REW-02..10 + BE-REF-01..03 + BE-SEA-01..03 focused tests | 431 passed | 431 passed |
| `makemigrations --check --dry-run` | No changes detected | |

## 18. Promotion Campaign Types and Scheduling (BE-PRO-01)

Status: **DONE**

### Requirement

Introduce an admin-managed, typed, scheduled promotion campaign domain (`PromotionCampaign`) in `apps.promotions` supporting the three source-backed campaign families (`MONTHLY`, `SEASONAL`, `FLASH`), explicit timezone-aware half-open scheduling (`[starts_at, ends_at)`), administrative enable/disable control, derived effective status (`DISABLED`, `SCHEDULED`, `ACTIVE`, `ENDED`), and a canonical `active_campaigns(now=None, campaign_type=None)` query.

**Scope Boundary:** BE-PRO-01 implements campaign types, scheduling, lifecycle status derivation, active-window queries, and manager CRUD/enable/disable APIs only. Service-level economic application (package discounts, auction-highlight discounts, Loft subscription discounts, PP multipliers, stacking/precedence/conflict resolution) belongs to **BE-PRO-02**, and the onboarding 20% first auction-highlight discount belongs to **BE-PRO-03**.

### Pre-change findings

1. `apps.promotions` previously contained `DiscountOffer` and `CashbackOffer` (inheriting from `OfferBase`), `UserPromotionGrant`, and `CashbackTransaction`.
2. `DiscountOffer` and `CashbackOffer` are marketplace order-item offers (`target_type` in `all`, `category`, `asset`), toggled manually via `is_active` with no `starts_at` / `ends_at` scheduling and no campaign types (`MONTHLY`, `SEASONAL`, `FLASH`).
3. Adding `PromotionCampaign` alongside `DiscountOffer` and `CashbackOffer` cleanly establishes scheduled promotion campaigns without altering marketplace order-item discount/cashback behavior or `UserPromotionGrant`.
4. `PromotionCampaign(campaign_type=SEASONAL)` is completely decoupled from `apps.seasons.Season` (seller competition seasons).

### Domain Model (`apps/promotions/models.py`)

- `PromotionCampaignType(models.TextChoices)`:
  - `MONTHLY = "MONTHLY", _("Monthly")`
  - `SEASONAL = "SEASONAL", _("Seasonal")`
  - `FLASH = "FLASH", _("Flash")`
- `PromotionCampaignStatus(models.TextChoices)` (derived at read/evaluation time):
  - `DISABLED = "DISABLED", _("Disabled")`
  - `SCHEDULED = "SCHEDULED", _("Scheduled")`
  - `ACTIVE = "ACTIVE", _("Active")`
  - `ENDED = "ENDED", _("Ended")`
- `PromotionCampaign(BaseModel)`:
  - `name = CharField(max_length=255)`
  - `campaign_type = CharField(max_length=20, choices=PromotionCampaignType.choices, db_index=True)`
  - `starts_at = DateTimeField()` (timezone-aware required)
  - `ends_at = DateTimeField()` (timezone-aware required)
  - `is_enabled = BooleanField(default=True, db_index=True)`
  - `description = TextField(blank=True, default="")`
  - `metadata = JSONField(default=dict, blank=True)`
  - `created_by = ForeignKey("users.Profile", on_delete=SET_NULL, null=True, blank=True, related_name="created_promotion_campaigns")`
  - DB CheckConstraint: `promo_campaign_starts_before_ends_ck` (`starts_at < ends_at`)
  - Indexes: `promo_campaign_active_win_idx` (`is_enabled`, `starts_at`, `ends_at`), `promo_campaign_type_win_idx` (`campaign_type`, `starts_at`, `ends_at`)
  - Ordering: `["starts_at", "id"]`

### Scheduling, Lifecycle, Overlap, and Editability Rules (`PromotionCampaignService`)

- **Half-open window (`[starts_at, ends_at)`):**
  - `now < starts_at` (when `is_enabled=True`) $	o$ `SCHEDULED`
  - `starts_at <= now < ends_at` (when `is_enabled=True`) $	o$ `ACTIVE`
  - `now >= ends_at` (when `is_enabled=True`) $	o$ `ENDED`
  - `is_enabled=False` (at any `now`) $	o$ `DISABLED`
- **Schedule-driven duration:** `FLASH` (and `MONTHLY` / `SEASONAL`) durations are purely defined by `starts_at` and `ends_at` (24h, 48h, 12h, or any valid window where `starts_at < ends_at`). No duration or discount percentage is hard-coded.
- **Overlap policy:** Overlapping campaigns (across different types such as `MONTHLY` + `FLASH` or `SEASONAL` + `FLASH`, as well as same-type overlaps) are allowed at the scheduling layer and returned in deterministic `("starts_at", "id")` order. Precedence/conflict resolution when applying economic effects belongs to BE-PRO-02.
- **Editability rules (`PromotionCampaignService.update`):**
  - Before `starts_at` (`now < campaign.starts_at`): `name`, `campaign_type`, `starts_at`, `ends_at`, `is_enabled`, `description`, and `metadata` are all editable (subject to `starts_at < ends_at` and timezone-awareness).
  - Once started (`now >= campaign.starts_at`, including active and ended campaigns): material schedule and type fields (`campaign_type`, `starts_at`, `ends_at`) are locked and rejected with `ValidationError`. Descriptive fields (`name`, `description`, `metadata`) and `is_enabled` (plus idempotent `enable`/`disable` actions) remain controllable without corrupting historical schedules or accidentally reactivating ended windows.
- **Zero economic side effects:** Campaign CRUD, enable/disable, `effective_status`, and `active_campaigns` create `0` `PointTransaction` rows, `0` notifications, and do not mutate package, highlight, subscription, or order pricing.

### API & Permissions

| Method | Path | Access / Permission | Behavior |
| --- | --- | --- | --- |
| `GET` | `/api/promotions/active/` | `IsAuthenticated` | Lists currently active (`is_enabled=True`, `starts_at <= now < ends_at`) campaigns using `PublicPromotionCampaignSerializer`; optional `?campaign_type=` filter |
| `GET` | `/api/promotions/admin/campaigns/` | `promotions.campaigns.list` | Lists campaigns with optional `campaign_type`, `is_enabled`, and `effective_status` filters |
| `POST` | `/api/promotions/admin/campaigns/` | `promotions.campaigns.create` | Creates a scheduled campaign and records `created_by` |
| `GET` | `/api/promotions/admin/campaigns/<id>/` | `promotions.campaigns.retrieve` | Retrieves one campaign |
| `PATCH` | `/api/promotions/admin/campaigns/<id>/` | `promotions.campaigns.update` | Updates a campaign according to pre-start / post-start editability rules |
| `POST` | `/api/promotions/admin/campaigns/<id>/enable/` | `promotions.campaigns.enable` | Idempotently enables a campaign |
| `POST` | `/api/promotions/admin/campaigns/<id>/disable/` | `promotions.campaigns.disable` | Idempotently disables a campaign |

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/promotions/models.py` | Added `PromotionCampaignType`, `PromotionCampaignStatus`, and `PromotionCampaign` | Scheduled campaign domain model and constraints |
| `apps/promotions/migrations/0003_promotion_campaign.py` | Created `PromotionCampaign` table, indexes, and `promo_campaign_starts_before_ends_ck` constraint | Database schema |
| `apps/promotions/services.py` | Added `PromotionCampaignService` (`create`, `update`, `enable`, `disable`, `effective_status`, `active_campaigns`) | Scheduling, lifecycle, editability, and active-window query |
| `apps/promotions/serializers.py` | Added `TimezoneAwareDateTimeField`, `PromotionCampaignSerializer`, and `PublicPromotionCampaignSerializer` | API validation and serialization |
| `apps/promotions/views.py`, `apps/promotions/urls.py` | Added `AdminPromotionCampaignViewSet`, `AdminPromotionCampaignActivationViewSet`, `ActivePromotionCampaignViewSet`, and routes | Manager CRUD/toggle and active campaign endpoints |
| `apps/users/manager_permissions.py` | Registered `promotions.campaigns.list`, `retrieve`, `create`, `update`, `enable`, `disable` | Exact manager action permissions |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | Added Arabic translations for promotion campaign messages and permission labels | Localization |
| `tests/promotions/test_promotion_campaigns.py` | New, 35 tests (`test_01` through `test_35`) | Full BE-PRO-01 coverage |
| `AGENTS.md`, `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md`, `docs/rewards/rewarding-system-report.md` | Documented BE-PRO-01 architecture, boundaries, and test results | Keep canonical docs aligned with code |

### Test results (2026-10-05, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/promotions/test_promotion_campaigns.py -q` | 35 passed |
| `pytest tests/promotions -q` | 63 passed |
| `pytest tests/seasons -q` | 127 passed |
| `pytest tests/referrals -q` | 114 passed |
| `pytest tests/rewards_sprint -q` | 8 passed |
| `pytest tests/test_i18n.py -q` | 7 passed |
| Combined BE-REW-02..10 + BE-REF-01..03 + BE-SEA-01..03 + BE-PRO-01 + permissions/docs/i18n | 538 passed |
| Scratch SQLite real migration apply, constraints, rollback (`0002`), and re-apply (`0003`) | PASSED |

## 19. Service-Level Promotion Effects (BE-PRO-02)

Status: **DONE**

### Requirement

Introduce service-level percentage discount effects (`PromotionCampaignEffect`) attached to scheduled `PromotionCampaign` windows (`MONTHLY`, `SEASONAL`, `FLASH`), a canonical non-stacking highest-discount pricing resolver (`PromotionPricingService` / `PromotionEffectService`), pre-start immutability once `timezone.now() >= campaign.starts_at`, and integration into canonical **Seller Package** (`apps.packages`) and **Loft Subscription** (`apps.loft_subscriptions`) pricing and purchase flows with durable snapshots.

**Scope Boundary:**
- BE-PRO-02 implements `PERCENT_DISCOUNT` (`0 < discount_percent <= 100`, Decimal math) for `SELLER_PACKAGE`, `AUCTION_HIGHLIGHT`, and `LOFT_SUBSCRIPTION`.
- **PP Multiplier Decision Gate:** Executable `POINTS_MULTIPLIER` behavior is intentionally omitted in BE-PRO-02 because canonical product docs do not define which reward codes/events a seasonal campaign "double points" promotion affects. `RewardEngine` and BE-REW-08 (`DEAL_COMPLETED`, multiplier 2) remain untouched.
- **Onboarding 20% First Highlight Discount:** Deferred to **BE-PRO-03**.

### Pre-change Service-Pricing Audit

1. **Seller Packages (`apps.packages`):**
   - Canonical price model: `Package` (`Package.price`, `DecimalField(max_digits=10, decimal_places=2)`).
   - Purchase/activation service: `SellerPackageService.request_package(profile, package_id)` creates a `PENDING` `AssetSellerPackage` (subclass of `SellerPackage`); `SellerPackageService.activate_package(seller_package)` activates it.
   - Durable snapshot: `SellerPackagePromotionSnapshot` (`OneToOneField("packages.SellerPackage", related_name="promotion_snapshot")`) stores `promotion_campaign`, `promotion_effect`, `base_price`, `discount_percent`, `discount_amount`, and `final_price` when `request_package` runs, without mutating `Package.price`.
2. **Loft Subscriptions (`apps.loft_subscriptions`):**
   - Canonical price models: `SubscriptionDurationOption`, `PedigreeTemplatePackage`, `SubscriptionAddon`, `AIQuotaPackage` (all `DecimalField(max_digits=10, decimal_places=2)`, `currency="EGP"`).
   - Pricing/checkout service: `SubscriptionPricingService.calculate(profile=..., validated_selection=...)` and `SubscriptionCheckoutService.execute(...)`.
   - Durable snapshot: `SubscriptionInvoice` stores `subtotal` (base amount), `total` (final discounted amount), and `pricing_snapshot["promotion"]` (`promotion_campaign_id`, `promotion_effect_id`, `base_amount`, `discount_percent`, `discount_amount`, `final_amount`); `SubscriptionPaymentRequest.amount` receives `invoice.total`.
3. **Auction Highlight (`apps.auctions`):**
   - `apps.auctions` does not yet have a canonical highlight/featured/boost model, price field, or paid purchase service.
   - `PromotionTargetType.AUCTION_HIGHLIGHT` is supported at the effect model, admin API, and `PromotionPricingService.resolve_service_discount(...)` level (`target_code=""` for all highlights or `target_code="<code>"` for a specific highlight code); purchase integration is pending a canonical paid highlight flow.

### Domain Models (`apps/promotions/models.py`)

- `PromotionEffectType(models.TextChoices)`:
  - `PERCENT_DISCOUNT = "PERCENT_DISCOUNT", _("Percentage Discount")`
- `PromotionTargetType(models.TextChoices)`:
  - `SELLER_PACKAGE = "SELLER_PACKAGE", _("Seller Package")`
  - `AUCTION_HIGHLIGHT = "AUCTION_HIGHLIGHT", _("Auction Highlight")`
  - `LOFT_SUBSCRIPTION = "LOFT_SUBSCRIPTION", _("Loft Subscription")`
- `PromotionCampaignEffect(BaseModel)`:
  - `campaign = ForeignKey(PromotionCampaign, on_delete=CASCADE, related_name="effects")`
  - `effect_type = CharField(max_length=30, choices=PromotionEffectType.choices, default=PERCENT_DISCOUNT)`
  - `target_type = CharField(max_length=30, choices=PromotionTargetType.choices)`
  - `target_package = ForeignKey("packages.Package", on_delete=PROTECT, null=True, blank=True)`
  - `target_subscription_duration = ForeignKey("loft_subscriptions.SubscriptionDurationOption", on_delete=PROTECT, null=True, blank=True)`
  - `target_code = CharField(max_length=80, blank=True, default="")`
  - `discount_percent = DecimalField(max_digits=5, decimal_places=2)` (`0 < discount_percent <= 100`, float values rejected)
  - `description = TextField(blank=True, default="")`
  - `metadata = JSONField(default=dict, blank=True)`
  - `created_by = ForeignKey("users.Profile", on_delete=SET_NULL, null=True, blank=True)`
  - DB CheckConstraints: `promo_eff_discount_pct_range_ck`, `promo_eff_target_fk_matches_type_ck`
  - Indexes: `promo_eff_camp_target_idx`, `promo_eff_target_disc_idx`
- `SellerPackagePromotionSnapshot(BaseModel)`:
  - `seller_package = OneToOneField("packages.SellerPackage", on_delete=CASCADE, related_name="promotion_snapshot")`
  - `package = ForeignKey("packages.Package", on_delete=PROTECT)`
  - `promotion_campaign = ForeignKey(PromotionCampaign, on_delete=SET_NULL, null=True, blank=True)`
  - `promotion_effect = ForeignKey(PromotionCampaignEffect, on_delete=SET_NULL, null=True, blank=True)`
  - `base_price`, `discount_percent`, `discount_amount`, `final_price`
  - DB CheckConstraint: `promo_pkg_snap_amounts_non_neg_ck`

### Resolution, Conflict, Immutability, and Snapshot Rules

- **Canonical active window:** `PromotionPricingService.resolve_service_discount` evaluates only effects whose campaign is returned by `PromotionCampaignService.active_campaigns(now=now)` (`is_enabled=True` and `starts_at <= now < ends_at`).
- **Non-stacking highest-discount resolution:** Multiple overlapping active campaigns or matching all-target + specific-target effects never stack additively or multiplicatively. Exactly one winning effect is chosen by deterministic ordering:
  1. highest `discount_percent` (`-discount_percent`)
  2. earlier `campaign.starts_at`
  3. lower `campaign.id`
  4. lower `effect.id`
- **Exact Decimal math:**
  - `discount_amount = (base_price * discount_percent / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` (capped at `base_price`)
  - `final_price = max(Decimal("0.00"), base_price - discount_amount)`
- **Effect immutability:** Effects can be created, updated, or deleted only while `now < campaign.starts_at`. Once `now >= campaign.starts_at`, effect mutations are blocked at model, service, and API layers.
- **Durable purchase snapshots:** Both `SellerPackagePromotionSnapshot` (for `SellerPackageService.request_package`) and `SubscriptionInvoice.pricing_snapshot["promotion"]` (for `SubscriptionCheckoutService.execute`) freeze the resolved pricing at purchase/request time; subsequent campaign expiry or disablement never alters existing purchase records.

### Admin API & Permissions

| Method | Path | Permission | Behavior |
| --- | --- | --- | --- |
| `GET` | `/api/promotions/admin/campaigns/<campaign_id>/effects/` | `promotions.campaign_effects.list` | Lists non-deleted effects for the campaign |
| `POST` | `/api/promotions/admin/campaigns/<campaign_id>/effects/` | `promotions.campaign_effects.create` | Creates an effect before `campaign.starts_at` |
| `GET` | `/api/promotions/admin/campaigns/<campaign_id>/effects/<id>/` | `promotions.campaign_effects.retrieve` | Retrieves one effect |
| `PATCH` | `/api/promotions/admin/campaigns/<campaign_id>/effects/<id>/` | `promotions.campaign_effects.update` | Updates an effect before `campaign.starts_at` |
| `DELETE` | `/api/promotions/admin/campaigns/<campaign_id>/effects/<id>/` | `promotions.campaign_effects.delete` | Soft-deletes an effect before `campaign.starts_at` |

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/promotions/models.py` | Added `PromotionEffectType`, `PromotionTargetType`, `PromotionCampaignEffect`, and `SellerPackagePromotionSnapshot` | Service-level effect domain and seller-package price snapshot |
| `apps/promotions/migrations/0004_promotion_campaign_effect.py` | Created `PromotionCampaignEffect` and `SellerPackagePromotionSnapshot` tables, indexes, and constraints | Database schema |
| `apps/promotions/services.py` | Added `ServiceDiscountResolution`, `PromotionCampaignEffectService`, and `PromotionPricingService` (`PromotionEffectService`) | Effect CRUD, pre-start freeze, and non-stacking discount resolution |
| `apps/promotions/serializers.py` | Added `PromotionCampaignEffectSerializer` | API payload validation and serialization |
| `apps/promotions/views.py`, `apps/promotions/urls.py` | Added `AdminPromotionCampaignEffectViewSet` and `/api/promotions/admin/campaigns/<campaign_id>/effects/` routes | Manager CRUD endpoints |
| `apps/users/manager_permissions.py` | Registered `promotions.campaign_effects.list`, `retrieve`, `create`, `update`, `delete` | Exact manager action permissions |
| `apps/packages/models.py`, `apps/packages/services.py`, `apps/packages/serializers.py` | Added `SellerPackageService.quote_package`, snapshot creation in `request_package`, and `SellerPackage` pricing snapshot properties | Seller package promotion integration |
| `apps/loft_subscriptions/services.py` | Integrated `PromotionPricingService.resolve_service_discount` into `SubscriptionPricingService.calculate` and allowed 100%-discounted paid invoice confirmation | Loft subscription promotion integration |
| `locale/ar/LC_MESSAGES/django.po` | Added Arabic translations for BE-PRO-02 validation errors and permission labels | Localization |
| `tests/promotions/test_promotion_campaign_effects.py` | New, 51 tests (`test_01` through `test_51`) | Full BE-PRO-02 coverage |

### Test results (2026-10-05, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/promotions/test_promotion_campaign_effects.py -q` | 51 passed |
| `pytest tests/promotions -q` | 114 passed |
| `pytest tests/loft_subscriptions -q` | 15 passed |
| `pytest tests/seasons -q` | 127 passed |
| `pytest tests/referrals -q` | 114 passed |
| Combined BE-PRO-01/02 + BE-SEA-01..03 + BE-REF-01..03 + BE-REW-02..10 + Loft Subscriptions + manager permissions | 562 passed |
| Scratch SQLite real migration apply (`0004_promotion_campaign_effect`) and `makemigrations --check --dry-run` | PASSED |

## 20. Onboarding 20% First Auction-Highlight Discount (BE-PRO-03)

Status: **DONE (actual purchase wiring deferred pending canonical highlight domain)**

### Requirement

Provide a one-time, account-level (`UserAccount`) **20% discount on the first paid auction highlight** (`ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT = Decimal("20.00")`) created during account onboarding, with non-consuming pricing preview (`quote`), non-stacking highest-discount comparison against active `AUCTION_HIGHLIGHT` promotion campaigns (`PromotionPricingService.resolve_service_discount`), equal-value tie resolution in favor of the onboarding benefit (`ONBOARDING_FIRST_HIGHLIGHT`), trusted internal atomic consumption (`consume_if_selected` / `commit_highlight_benefit`), and a safe authenticated read-only endpoint (`GET /api/promotions/onboarding/highlight-benefit/`).

### Pre-change Auction-Highlight Flow Audit

1. A repo-wide inspection of `apps.auctions`, `apps.promotions`, `apps.packages`, `apps.payments`, `apps.orders`, `apps.users`, and `apps.authentication` confirmed **Decision B — NO CANONICAL HIGHLIGHT PURCHASE FLOW**:
   - `apps.auctions` has `Auction`, `AuctionItem`, `Bid`, and `AuctionEvent` only; there is no highlight/featured/boost model, price field, or paid highlight checkout/confirmation service.
   - `apps.insights` explicitly notes that promoted auction counts return `0` because no promoted listing model exists.
2. In accordance with the architecture boundary, BE-PRO-03 does **not** invent a fake `HighlightPurchase`, `AuctionHighlightOrder`, or `PaymentRequest` flow. Instead, it implements the canonical account-level entitlement, quote/comparison engine, trusted internal atomic consumption service (`OnboardingHighlightBenefitService.consume_if_selected` / `commit_highlight_benefit`), and safe read-only API, leaving actual highlight checkout wiring deferred until the canonical highlight purchase domain is introduced.

### Domain Reuse & Zero-Migration Design (`apps/promotions/models.py`, `apps/promotions/services.py`)

- **Option A — Reused `UserPromotionGrant` (No Schema Migration):**
  - Each onboarded `UserAccount` receives at most one `UserPromotionGrant` row:
    - `user = <UserAccount>`
    - `profile = <initial triggering Profile or None>` (attribution only; ownership is strictly `UserAccount`-scoped)
    - `grant_type = UserPromotionGrantType.DISCOUNT`
    - `source_type = OfferTriggerType.ONBOARDING`
    - `discount_offer = <system inactive DiscountOffer(title="Onboarding First Auction Highlight 20% Discount", trigger_type="onboarding", value_type="percentage", value=Decimal("20.00"), target_type="all", is_active=False)>`
    - `status = UserPromotionGrantStatus.ACTIVE` (available) $\to$ `UserPromotionGrantStatus.REDEEMED` (consumed)
    - `idempotency_key = f"promotion:onboarding_highlight:user:{user.pk}"` (unique DB constraint)
    - `metadata = {"benefit_code": "onboarding_first_auction_highlight_20_pct", "target_type": "AUCTION_HIGHLIGHT", "discount_percent": "20.00", ...}`
  - Because `discount_offer.is_active = False` and `trigger_type = "onboarding"`, normal marketplace order checkout (`DiscountOfferService.choose_best_offer`, which filters `is_active=True, trigger_type="global"`) and `GET /api/promotions/my-grants/` (which filters `discount_offer__is_active=True`) never accidentally apply or expose this grant as a marketplace order discount.
  - Non-schema convenience properties on `UserPromotionGrant`: `consumed_at` (`redeemed_at`), `discount_percent`, `is_available`, and `is_consumed`.

### Account-Level Onboarding Creation & No-Backfill Policy (Hardened in BE-PRO-03A)

- **Automatic profile-creation hook (`OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding`):**
  1. `CustomerRegisterSerializer.create` (`POST /api/auth/register/customer/`)
  2. `SellerProfileMobileViewSet.create` (`POST /api/users/mobile/sellers/`)
  3. `CustomerProfileMobileViewSet.create` (`POST /api/users/mobile/customers/`)
  - Low-level `OnboardingHighlightBenefitService.ensure_for_onboarding(user, profile=None)` remains available as the trusted idempotent grant creator for explicit internal callers.
- **First historical `Customer`/`Seller` profile rule (`has_prior_onboarding_profile_history`):**
  - Automatic grant creation evaluates `Profile.all_objects.filter(user_id=user.pk, type__in=(ProfileType.CUSTOMER, ProfileType.SELLER))` (including soft-deleted rows) under `UserAccount` row lock.
  - A grant is created **only** when the account has exactly **1** historical `Customer` or `Seller` profile row in `Profile.all_objects`, that row is not soft-deleted (`deleted__isnull=True`), and it matches the newly created profile.
  - **Fresh Customer first or Seller first:** receives 1 grant.
  - **Fresh Customer $\to$ Seller later, or Fresh Seller $\to$ Customer later:** second mode creation sees `count == 2 > 1` and is a no-op; total grants remain 1 and a consumed grant is never reset.
  - **Historical pre-BE-PRO-03 account with existing Customer or Seller profile creating a second mode later:** sees `count == 2 > 1` and receives **0** grants (profile switching or later mode creation can never backfill historical accounts).
  - **Soft-deleted profile history:** `Profile.all_objects` preserves soft-deleted prior profiles, so deleting a profile and creating a new `Customer` or `Seller` profile still counts as already onboarded and receives **0** grants.
  - Manager accounts (`ManagerProfile`) are excluded and never receive the benefit.
- **No read/quote/consume or mode-creation backfill:**
  - Neither read/quote/consume methods (`get_benefit`, `get_benefit_status`, `is_available`, `quote`, `consume_if_selected`) nor profile switching / later mode creation can backfill historical accounts.
- **`UserPromotionGrant.discount_percent` hardening (BE-PRO-03A):**
  - `UserPromotionGrant.discount_percent` derives its value from the associated percentage `DiscountOffer` (with a `Decimal("20.00")` fallback scoped strictly to `is_onboarding_highlight_benefit`), so other hypothetical or future `source_type == OfferTriggerType.ONBOARDING` grants (e.g. a 10% offer, fixed-amount offer, or cashback grant) are never misreported as 20%.

### Quote, Campaign Comparison, Tie-Break, and Atomic Consumption (`OnboardingHighlightBenefitService`)

- **Fixed 20% & Decimal Math:**
  - `ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT = Decimal("20.00")` (fixed, non-configurable).
  - Rejects `bool`, `float`, negative, or non-numeric `base_amount` inputs with `ValidationError`.
  - Free/zero-price highlight (`base_amount == Decimal("0.00")`) returns `applied_source = NONE`, `discount_percent = 0.00`, `final_amount = 0.00`, and `consumes_onboarding_benefit = False`.
- **Campaign vs Onboarding Resolution (`quote`):**
  - Calls `PromotionPricingService.resolve_service_discount(target_type=PromotionTargetType.AUCTION_HIGHLIGHT, base_price=base_amount, target_code=target_code, now=now)`.
  - Compares the active campaign discount (`campaign_resolution.discount_percent`) against the available onboarding 20% benefit:
    - **No active campaign (or 0%) + onboarding available:** `ONBOARDING_FIRST_HIGHLIGHT` (`20.00%`, `consumes_onboarding_benefit=True`).
    - **Active campaign < 20% (e.g. 10%) + onboarding available:** `ONBOARDING_FIRST_HIGHLIGHT` (`20.00%` wins; no stacking; consumes on commit).
    - **Active campaign > 20% (e.g. 30% or 100%) + onboarding available:** `CAMPAIGN` wins (`30.00%` / `100.00%`; `consumes_onboarding_benefit=False`; onboarding benefit remains `ACTIVE` for a future paid highlight).
    - **Active campaign == 20% + onboarding available (Equal-Value Tie Policy):** `ONBOARDING_FIRST_HIGHLIGHT` wins the tie (`20.00%`, `consumes_onboarding_benefit=True`), consuming the one-time onboarding entitlement on commit.
    - **Onboarding already consumed or absent:** falls back to `CAMPAIGN` if active, else `NONE`.
- **Trusted Atomic Consumption (`consume_if_selected` / `commit_highlight_benefit`):**
  - Runs inside `transaction.atomic()`, locking the user's `UserPromotionGrant` row (`select_for_update()`) and re-evaluating `quote(...)` under the lock.
  - Consumes the grant (`status = REDEEMED`, `redeemed_at = commit_time`, `metadata["consumed_source_id"] = source_id`, `metadata["consumption_snapshot"] = {...}`) **only** when `quote.applied_source == ONBOARDING_FIRST_HIGHLIGHT` and `quote.consumes_onboarding_benefit is True`.
  - Idempotent on retry with the same `source_id`: returns the stored `OnboardingHighlightQuoteResult` snapshot without double-consuming or failing.
  - Reusing a consumed benefit with a different `source_id` when no campaign applies returns `NONE` (`0%` discount) and does not re-consume.
  - Rolls back cleanly if the surrounding transaction raises an exception after `consume_if_selected`.
- **Zero PP / Notification Side Effects:**
  - `ensure_for_onboarding`, `get_benefit_status`, `quote`, and `consume_if_selected` create `0` `PointTransaction` rows and `0` `Notification` rows, and do not modify `RewardEngine`.

### Read-Only API

| Method | Path | Access / Permission | Behavior |
| --- | --- | --- | --- |
| `GET` | `/api/promotions/onboarding/highlight-benefit/` | `IsAuthenticated` (`OnboardingHighlightBenefitViewSet`) | Non-mutating status read for `request.user`: returns `has_benefit`, `is_available`, `is_consumed`, `discount_percent` (`"20.00"`), `target_type` (`"AUCTION_HIGHLIGHT"`), `consumed_at`, and `source_id`. Never backfills or consumes. |

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/promotions/models.py` | Added non-schema convenience properties (`is_onboarding_highlight_benefit`, `consumed_at`, `discount_percent`, `is_available`, `is_consumed`) to `UserPromotionGrant` | Clean domain access without a schema migration; `discount_percent` derived from percentage `DiscountOffer` / `is_onboarding_highlight_benefit` |
| `apps/promotions/services.py` | Added `ONBOARDING_HIGHLIGHT_DISCOUNT_PERCENT`, `OnboardingHighlightDiscountSource`, `OnboardingHighlightQuoteResult`, `OnboardingHighlightBenefitService` (`ensure_for_onboarding`, `has_prior_onboarding_profile_history`, `ensure_for_first_profile_onboarding`, `quote`, `consume_if_selected`), and `UserPromotionGrantService` helpers | Account-level onboarding entitlement, first-profile no-backfill boundary, quote/campaign comparison, and atomic consumption |
| `apps/authentication/serializers.py` | Wired `OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding` into `CustomerRegisterSerializer.create` | Grant benefit on fresh customer registration |
| `apps/users/views.py` | Wired `OnboardingHighlightBenefitService.ensure_for_first_profile_onboarding` into `SellerProfileMobileViewSet.create` and `CustomerProfileMobileViewSet.create` | Grant only on first-ever Customer/Seller profile; never backfill historical accounts on later mode creation |
| `apps/promotions/serializers.py`, `apps/promotions/views.py`, `apps/promotions/urls.py` | Added `OnboardingHighlightBenefitSerializer`, `OnboardingHighlightBenefitViewSet`, and `GET /api/promotions/onboarding/highlight-benefit/` | Safe read-only benefit status API |
| `tests/promotions/test_onboarding_highlight_benefit.py` | New, 72 tests (`test_01` through `test_72`) | Full BE-PRO-03 + BE-PRO-03A coverage |
| `AGENTS.md`, `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md`, `docs/rewards/rewarding-system-report.md` | Documented BE-PRO-03 + BE-PRO-03A architecture, decision gate, and test results | Keep canonical docs aligned with code |

### Test results (2026-10-05, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/promotions/test_onboarding_highlight_benefit.py -q` | 72 passed |
| `pytest tests/promotions -q` | 186 passed |
| `pytest tests/promotions tests/loft_subscriptions tests/seasons tests/referrals tests/rewards_sprint -q` | 434 passed |
| `pytest tests/packages tests/auctions tests/loyalty -q` (`LANGUAGE_CODE="en"`) | 313 passed, 1 skipped |
| `python manage.py makemigrations --check --dry-run` | No changes detected |

## 21. Weekly Onboarding Draw Domain & Automatic Entry (BE-DRW-01)

Status: **DONE**

### Requirement

Implement the dedicated **Weekly Onboarding Draw** domain (`apps.draws`: `WeeklyDraw`, `WeeklyDrawEntry`) and automatic account-level onboarding entry (`WeeklyDrawEntryService.ensure_for_first_profile_onboarding`), cleanly separated from the Lucky Wheel (`apps.lucky_wheel`), so that each newly onboarded `UserAccount` receives at most **1** onboarding weekly-draw entry across `Customer` and `Seller` modes without retroactive backfill for historical accounts.

**Scope Boundary:**
- BE-DRW-01 owns draw scheduling (`WeeklyDraw`), account-level onboarding draw entry creation (`WeeklyDrawEntry`), pending-entry assignment (`WeeklyDrawService.assign_pending_entries`), manager draw lifecycle APIs (`list`, `retrieve`, `create`, `update`, `open`, `close`, `entries`), and the user read-only status endpoint (`GET /api/draws/my-weekly-entry/`).
- Winner selection, randomness, finalization (`FINALIZED`), prize definitions/fulfillment, PP Coin transactions, notifications, and `PaymentRequest` rows are out of scope for BE-DRW-01 (winner selection belongs to **BE-DRW-02**).

### Pre-change Audit & Lucky Wheel Separation Decision

1. **Product Source Audit (`REQUIREMENTS_AUDIT_SECTION_6.md` §6.1 vs `REQUIREMENTS_AUDIT_SECTION_13.md`):**
   - Section 6.1 defines the three account onboarding rewards:
     1. `100 PP` (`RewardEngine` / `ACCOUNT_ONBOARDING`)
     2. `20%` discount on first auction highlight (`OnboardingHighlightBenefitService`)
     3. Automatic weekly draw entry (`WeeklyDrawEntryService` in `apps.draws`)
   - Section 13 defines the **Lucky Wheel** (`apps.lucky_wheel`) as a separate 8-slot spin wheel triggered by auction completion and other non-onboarding sources.
2. **Resolution of Earlier Onboarding Lucky Wheel Conflation:**
   - Before `apps.draws` existed, the initial MVP wired `LuckyWheelGrantService.grant_onboarding_attempt(profile)` into `CustomerRegisterSerializer.create`, `SellerProfileMobileViewSet.create`, and `CustomerProfileMobileViewSet.create`, which granted profile-scoped spin attempts (`lucky_wheel:onboarding:{profile_id}`).
   - In BE-DRW-01, `LuckyWheelGrantService.grant_onboarding_attempt(...)` is removed from those three onboarding hooks and replaced with `WeeklyDrawEntryService.ensure_for_first_profile_onboarding(user=..., profile=...)`.
   - `LuckyWheelGrantSourceType.ONBOARDING` and `LuckyWheelGrantService.grant_onboarding_attempt` remain on the model/service for historical row compatibility, while genuine Lucky Wheel grant paths (`grant_for_auction_completed`, `grant_attempt`) and spin flows remain unchanged.

### Domain Models (`apps/draws/models.py`)

- `WeeklyDrawStatus(models.TextChoices)`:
  - `DRAFT = "DRAFT"`, `OPEN = "OPEN"`, `CLOSED = "CLOSED"`, `FINALIZED = "FINALIZED"` (reserved for BE-DRW-02)
- `WeeklyDraw(BaseModel)`:
  - `title` (`CharField(max_length=150, blank=True, default="")`)
  - `starts_at`, `ends_at` (timezone-aware `DateTimeField`)
  - `status` (`CharField`, default `DRAFT`)
  - `created_by` (`ForeignKey("users.Profile", on_delete=SET_NULL, null=True, blank=True)`)
  - `metadata` (`JSONField(default=dict, blank=True)`)
  - DB CheckConstraint: `draws_weekly_starts_before_ends_ck` (`starts_at < ends_at`)
  - DB Indexes: `draws_weekly_status_start_idx`, `draws_weekly_window_idx`
  - Non-overlapping `OPEN` window invariant enforced on create, update, open, and `clean()`.
  - Schedule fields (`starts_at`, `ends_at`) are locked once `now >= starts_at`, once the draw has assigned entries, or once `status in (CLOSED, FINALIZED)`.
  - `delete()` raises `ProtectedError` when entries are attached (`on_delete=models.PROTECT` on `WeeklyDrawEntry.draw` plus soft-delete guard).
- `WeeklyDrawEntrySource(models.TextChoices)`:
  - `ONBOARDING = "ONBOARDING"`
- `WeeklyDrawEntryStatus(models.TextChoices)`:
  - `PENDING = "PENDING"`, `ASSIGNED = "ASSIGNED"`
- `WeeklyDrawEntry(BaseModel)`:
  - `draw` (`ForeignKey(WeeklyDraw, on_delete=PROTECT, null=True, blank=True, related_name="entries")`)
  - `user` (`ForeignKey("authentication.UserAccount", on_delete=CASCADE, related_name="weekly_draw_entries")`)
  - `source` (`CharField`, default `ONBOARDING`)
  - `status` (`CharField`, default `PENDING`)
  - `idempotency_key` (`CharField(max_length=160, unique=True)`, `"draw:onboarding:user:{user_id}"`)
  - `assigned_at` (`DateTimeField(null=True, blank=True)`)
  - `metadata` (`JSONField(default=dict, blank=True)`)
  - DB UniqueConstraint: `draws_entry_unique_user_source` on `("user", "source")`
  - DB CheckConstraint: `draws_entry_status_draw_consistency_ck` (`(status=PENDING AND draw IS NULL AND assigned_at IS NULL) OR (status=ASSIGNED AND draw IS NOT NULL AND assigned_at IS NOT NULL)`)
  - Once `status == ASSIGNED`, `draw` and `status` are immutable and cannot be reassigned or reverted to `PENDING`.

### Account-Level First-Profile No-Backfill & Draw Assignment (`apps/users/services.py`, `apps/draws/services.py`)

- **Shared First-Profile History Boundary (`AccountOnboardingService.has_prior_customer_seller_profile_history`):**
  - Evaluates `Profile.all_objects.filter(user_id=user.pk, type__in=(ProfileType.CUSTOMER, ProfileType.SELLER))` (including soft-deleted rows) under `UserAccount` row lock.
  - Shared by both `OnboardingHighlightBenefitService` and `WeeklyDrawEntryService`.
  - Grants an onboarding entry only when the account has exactly **1** historical `Customer` or `Seller` profile row in `Profile.all_objects`, that row is non-deleted, and it matches the newly created profile.
  - Second mode creation (`Customer -> Seller` or `Seller -> Customer`), historical pre-BE-DRW-01 accounts creating a second mode later, soft-deleted profile recreation, and `ManagerProfile` accounts never create or backfill entries.
- **Canonical Eligible Draw Selection (`WeeklyDrawService.choose_eligible_draw`):**
  - Half-open active window: `starts_at <= now < ends_at`.
  - **Rule A:** Prefer an `OPEN` draw where `starts_at <= now < ends_at` (`starts_at ASC, id ASC`).
  - **Rule B:** Otherwise choose the earliest upcoming (`starts_at > now`) draw with `status in (OPEN, DRAFT)` (`starts_at ASC, id ASC`).
  - **Rule C:** Never select ended (`ends_at <= now`), `CLOSED`, or `FINALIZED` draws.
- **Pending Entry Creation & Assignment (`WeeklyDrawService.assign_pending_entries`):**
  - If no eligible draw exists at onboarding time, `WeeklyDrawEntry` is created with `status=PENDING`, `draw=None`, `assigned_at=None`.
  - When an eligible draw is created or opened (`create_draw`, `open_draw`, `update_draw`, or explicit `assign_pending_entries`), all `PENDING` entries are locked with `select_for_update()` and assigned once (`status=ASSIGNED`, `draw=target_draw`, `assigned_at=now`).

### APIs & Permissions

| Method | Path | Access / Permission | Behavior |
| --- | --- | --- | --- |
| `GET` | `/api/draws/weekly/` | `draws.weekly.list` | Lists non-deleted weekly draws with `effective_status` and `entries_count` |
| `POST` | `/api/draws/weekly/` | `draws.weekly.create` | Creates a `DRAFT` or `OPEN` weekly draw and assigns pending entries if eligible |
| `GET` | `/api/draws/weekly/<id>/` | `draws.weekly.retrieve` | Retrieves one weekly draw |
| `PATCH` | `/api/draws/weekly/<id>/` | `draws.weekly.update` | Updates a draw (schedule fields locked once started or once entries are assigned) |
| `POST` | `/api/draws/weekly/<id>/open/` | `draws.weekly.open` | Opens a `DRAFT` draw (`ends_at > now`, non-overlapping) and assigns pending entries |
| `POST` | `/api/draws/weekly/<id>/close/` | `draws.weekly.close` | Closes an `OPEN` draw |
| `GET` | `/api/draws/weekly/<id>/entries/` | `draws.weekly.entries.list` | Lists entries assigned to the draw |
| `GET` | `/api/draws/my-weekly-entry/` | `IsAuthenticated` | Non-mutating read returning `has_entry`, `status`, `draw_id`, `draw_starts_at`, `draw_ends_at`, `assigned_at`; never backfills or leaks other entrants/internal keys |

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/users/services.py` | Added `AccountOnboardingService.has_prior_customer_seller_profile_history` | Shared neutral first-profile no-backfill helper for `apps.promotions` and `apps.draws` |
| `apps/promotions/services.py` | Delegated `OnboardingHighlightBenefitService.has_prior_onboarding_profile_history` to `AccountOnboardingService` | Single canonical first-profile history check |
| `apps/draws/models.py`, `apps/draws/migrations/0001_initial.py` | Added `WeeklyDraw`, `WeeklyDrawEntry`, enums, indexes, and DB constraints | Dedicated weekly onboarding draw domain |
| `apps/draws/services.py` | Added `WeeklyDrawService` and `WeeklyDrawEntryService` | Draw lifecycle, eligible-draw resolution, pending-entry assignment, and account-level onboarding entry creation |
| `apps/draws/serializers.py`, `apps/draws/views.py`, `apps/draws/urls.py` | Added manager draw/entry viewsets and `MyWeeklyDrawEntryViewSet` | Manager and user read-only APIs |
| `ppw/settings/base.py`, `ppw/urls.py` | Registered `apps.draws` and mounted `/api/draws/` | App and route wiring |
| `apps/users/manager_permissions.py`, `locale/ar/LC_MESSAGES/django.po`, `django.mo` | Registered `draws.weekly.*` permissions and Arabic translations | Manager RBAC and localization |
| `apps/authentication/serializers.py`, `apps/users/views.py` | Replaced `LuckyWheelGrantService.grant_onboarding_attempt` with `WeeklyDrawEntryService.ensure_for_first_profile_onboarding` | Replace misimplemented onboarding wheel attempt with canonical weekly draw entry |
| `tests/draws/test_weekly_onboarding_draws.py` | New, 57 tests (`test_01` through `test_57`) | Full BE-DRW-01 coverage |
| `tests/loyalty/test_account_onboarding_reward.py`, `tests/loyalty/test_reward_rule_config.py`, `tests/rewards_sprint/test_rewards_sprint.py` | Updated 3 onboarding assertions from `LuckyWheelGrant` to `WeeklyDrawEntry` | Align regression tests with onboarding Lucky Wheel separation |

### Test results (2026-10-05, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/draws/test_weekly_onboarding_draws.py -q` | 57 passed |
| `pytest tests/draws tests/rewards_sprint tests/loyalty/test_account_onboarding_reward.py tests/loyalty/test_reward_rule_config.py tests/users/test_manager_permissions.py -q` | 133 passed |
| `pytest tests/test_i18n.py tests/test_api_docs.py -q` | 13 passed |
| `pytest tests/draws tests/promotions tests/seasons tests/referrals tests/rewards_sprint tests/test_api_docs.py tests/test_i18n.py -q` | 505 passed |
| `python manage.py makemigrations --check --dry-run` | No changes detected |

## 22. Weekly Draw Finalization & Winner-Selection Requirement Gate (BE-DRW-02)

Status: **PARTIAL / BLOCKED** (Safe execution/freeze foundation implemented; winner selection blocked pending product decision)

### Source-of-Truth Gate & Requirement Verification Table

Audited sources:
1. Original Arabic product specification PDF (`App pigeon planet وثيقة نطاق المشروع ومواصفات التطبيق النهائية2.pdf`, Section 6.1 lines 768–778, Section 6.5 lines 838–853, Section 16.1 line 1521)
2. Badges & Reward Scenarios Report (`Pigeon_Planet_Badges_and_Reward_Scenarios_Report.pdf`)
3. Section 6 Requirements Audit (`backend remainings/REQUIREMENTS_AUDIT_SECTION_6.md`, §6.1.3)
4. `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md`, `docs/rewards/rewarding-system-report.md`, and `AGENTS.md`

| Requirement | Source-Confirmed? | Canonical Source Evidence |
| --- | --- | --- |
| Weekly onboarding draw exists | **YES** | Arabic PDF §6.1 (`جوائز ترحيبية (Onboarding)`): `دخول سحب أسبوعي تلقائي` ("automatic weekly draw entry") |
| Eligible population | **PARTIAL** (onboarding accounts; one-time next-eligible draw is a BE-DRW-01 technical decision) | Arabic PDF §6.1 specifies automatic entry upon new account registration (`عند تسجيل حساب جديد يحصل المستخدم على ... دخول سحب أسبوعي تلقائي`) |
| Number of winners | **NO (UNSPECIFIED)** | Neither §6.1 nor any other section defines whether a weekly onboarding draw selects 1 winner, 3 winners, or N winners |
| Winner selection algorithm | **NO (UNSPECIFIED)** | No random/weighted/manual selection rule is defined for the weekly onboarding draw |
| Equal probability / weighting | **NO (UNSPECIFIED)** | Not mentioned in the Arabic specification or badge/reward report |
| Execution time & weekday | **NO (UNSPECIFIED)** | No weekday or clock time is defined in the specification |
| Timezone | **NO (UNSPECIFIED)** | No draw execution timezone is defined in the specification |
| Prize type | **NO (UNSPECIFIED)** | §6.5 describes general digital vs financial/in-kind prize delivery, and §6.2–6.4 list prizes for Monthly Referral, Seasonal Seller, and Lucky Wheel, but no prize type is assigned to the weekly onboarding draw |
| Prize value | **NO (UNSPECIFIED)** | No PP amount, discount, cash amount, or physical item is specified for the weekly onboarding draw |
| Repeated winner eligibility | **NO (UNSPECIFIED)** | Not specified in the product documents |
| Whether manager manually executes | **NO (UNSPECIFIED)** | Not specified in the product documents |
| Whether system auto-executes | **NO (UNSPECIFIED)** | Not specified in the product documents |

### Separation of Confirmed Requirements, Technical Decisions, and Unresolved Product Questions

1. **SOURCE-CONFIRMED:**
   - New account onboarding grants an automatic weekly draw entry (`دخول سحب أسبوعي تلقائي`, §6.1).
   - Winning a draw eventually triggers a congratulatory in-app notification (`سحب، تصلها رسالة تهنئة داخل التطبيق` / `"تهانينا! أنت من الفائزين"`, §6.5 & §16.1; notification delivery belongs to `BE-NOT-*`, not `BE-DRW-02`).
2. **TECHNICAL DECISIONS (Implemented Foundation):**
   - **BE-DRW-01** one-time next-eligible draw assignment per `UserAccount` (`WeeklyDraw` half-open `[starts_at, ends_at)`, `WeeklyDrawEntry` with `PENDING` / `ASSIGNED`).
   - **BE-DRW-02** execution boundary: a `WeeklyDraw` can be closed or finalized only when `timezone.now() >= ends_at` (`starts_at <= now < ends_at` rejects closing/finalizing with `400 ValidationError`).
   - **BE-DRW-02** frozen participant set: at close/finalization, eligible entrants are strictly `WeeklyDrawEntry` rows with `draw = this draw`, `status = ASSIGNED`, and `deleted__isnull = True` ordered by `("created", "id")`. `PENDING` entries, entries from other draws, soft-deleted rows, Lucky Wheel attempts, referrals, and auction bids are excluded.
   - **BE-DRW-02** concurrency and idempotency: `WeeklyDrawService.close_draw` and `WeeklyDrawService.finalize_draw` (`WeeklyDrawFinalizationService.finalize_draw`) run inside `transaction.atomic()`, lock the `WeeklyDraw` row with `select_for_update()`, freeze the eligible participant snapshot once into `metadata["execution_freeze"]` (`eligible_entry_ids`, `eligible_user_ids`, `eligible_entries_count`, `frozen_at`, `frozen_by_profile_id`, `winner_selection_status="BLOCKED_PENDING_PRODUCT_DECISION"`, `winners_selected_count=0`), and set `status = CLOSED`.
   - **BE-DRW-02** status rule (Option A): because winner selection semantics are not product-confirmed, `finalize_draw` keeps `status = CLOSED` and does **not** transition `status` to `FINALIZED` (avoiding any false claim that a winner was selected). Repeated `finalize_draw` calls return the stored frozen `CLOSED` draw idempotently without mutating `frozen_at`, `frozen_by_profile_id`, or `eligible_entry_ids`.
   - **BE-DRW-02** immutability & deletion protection: once `CLOSED` or `FINALIZED`, a `WeeklyDraw` cannot be reopened, modified, or deleted (`ProtectedError`), and `ASSIGNED` `WeeklyDrawEntry` rows cannot be reassigned, reverted to `PENDING`, modified, or deleted (`ProtectedError`).
3. **UNRESOLVED (Requires Product Decision Before Winner Selection Can Be Implemented):**
   - Exact winner count per weekly onboarding draw (e.g., 1 winner vs multiple winners)
   - Exact winner-selection algorithm (e.g., unbiased cryptographic random draw vs weighted vs manual)
   - Exact weekly onboarding draw prize type and value
   - Exact execution schedule (manual manager trigger vs automated Celery schedule, weekday/time/timezone)

### APIs & Permissions Added / Updated in BE-DRW-02

| Method | Path | Access / Permission | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/draws/weekly/<id>/close/` | `draws.weekly.close` | Requires `status == OPEN` and `now >= ends_at`; locks draw, freezes `ASSIGNED` participant set into `metadata["execution_freeze"]`, transitions `status -> CLOSED` |
| `POST` | `/api/draws/weekly/<id>/finalize/` | `draws.weekly.finalize` | Idempotent execution freeze endpoint: rejects `DRAFT` and `now < ends_at`; locks draw with `select_for_update()`, freezes `ASSIGNED` participant set once, keeps `status = CLOSED` (`winner_selection_status = "BLOCKED_PENDING_PRODUCT_DECISION"`) |
| `GET` | `/api/draws/weekly/` & `/api/draws/weekly/<id>/` | `draws.weekly.list` / `draws.weekly.retrieve` | Includes read-only `is_participant_set_frozen`, `winner_selection_status`, `entries_count` (`ASSIGNED` non-deleted count), and `metadata.execution_freeze` |
| `GET` | `/api/draws/weekly/<id>/entries/` | `draws.weekly.entries.list` | Lists eligible `ASSIGNED` non-deleted entries for the draw ordered by `("created", "id")` |

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/draws/models.py` | Hardened `WeeklyDraw.clean()`, `WeeklyDraw.delete()`, `WeeklyDrawEntry.clean()`, and `WeeklyDrawEntry.delete()` | Prevent reopening/deleting `CLOSED`/`FINALIZED` draws and prevent mutating/deleting `ASSIGNED` entries |
| `apps/draws/services.py` | Updated `WeeklyDrawService.close_draw` and `update_draw` (`now >= ends_at` + participant freeze); added `get_eligible_entries`, `finalize_draw`, and `WeeklyDrawFinalizationService` | Atomic `select_for_update()` execution freeze, idempotent `finalize_draw`, and winner-selection requirement gate |
| `apps/draws/serializers.py`, `apps/draws/views.py`, `apps/draws/urls.py` | Added `WeeklyDrawFinalizeViewSet` (`POST /api/draws/weekly/<id>/finalize/`), `is_participant_set_frozen`, `winner_selection_status`, and `ASSIGNED` entry scoping | Manager finalization/freeze endpoint and read inspection |
| `apps/users/manager_permissions.py`, `locale/ar/LC_MESSAGES/django.po`, `django.mo` | Added `draws.weekly.finalize` permission and Arabic translations | Manager RBAC and Arabic-first localization |
| `tests/draws/test_weekly_onboarding_draws.py` | Updated `test_30`, `test_32`, `test_33`, `test_48` to close draws at `now >= ends_at` | Align BE-DRW-01 close calls with `now >= ends_at` execution boundary |
| `tests/draws/test_weekly_draw_finalization.py` | New, 26 tests (`test_01` through `test_26`) | Full BE-DRW-02 finalization/freeze, immutability, permission, and side-effect coverage |

### Test results (2026-10-06, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/draws/test_weekly_draw_finalization.py -q` | 26 passed |
| `pytest tests/draws/test_weekly_onboarding_draws.py -q` | 57 passed |
| `pytest tests/draws -q` | 83 passed |
| `pytest tests/draws tests/promotions tests/seasons tests/referrals tests/rewards_sprint tests/test_api_docs.py tests/test_i18n.py -q` | 531 passed |
| `python manage.py makemigrations --check --dry-run` | No changes detected |

## 23. Lucky Wheel Source Audit + Eligibility / Attempt Lifecycle Hardening (BE-LW-01 & BE-LW-01A)

Status: **DONE** (`BE-LW-01` & `BE-LW-01A`)

### Source-of-Truth Audit & Provenance Classification Table

Audited sources:
1. Original Arabic product specification PDF (`App pigeon planet وثيقة نطاق المشروع ومواصفات التطبيق النهائية2.pdf`, Section 6.1 lines 768–778, Section 11.4 line 1210, Section 13.2 lines 1408–1418)
2. Badges & Reward Scenarios Report (`Pigeon_Planet_Badges_and_Reward_Scenarios_Report.pdf`, Table C-09 lines 517–565)
3. Section 6 Requirements Audit (`backend remainings/REQUIREMENTS_AUDIT_SECTION_6.md`, §6.4 / §6.6)
4. `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md`, `docs/rewards/rewarding-system-report.md`, and `AGENTS.md`

Every Lucky Wheel rule is explicitly classified into one of three provenance categories:
- **SOURCE-CONFIRMED**: Explicitly stated in the Arabic product specification PDF or Badges & Reward Scenarios Report.
- **CURRENT IMPLEMENTATION / TECHNICAL DECISION**: Implemented in the repository as an engineering/product design choice, not explicitly mandated at that level of detail in the product PDF.
- **UNRESOLVED**: Not specified in the canonical product documents.

| Rule / Requirement | Source-Confirmed? | Provenance Category & Current Implementation |
| --- | --- | --- |
| Lucky Wheel exists | **YES (SOURCE-CONFIRMED)** | Arabic PDF §13.2 (`عجلة الحظ (The Lucky Wheel)`); Badges Report Scenario `C-09 Lucky Wheel` |
| Separate from Weekly Onboarding Draw | **YES (SOURCE-CONFIRMED)** | Arabic PDF §6.1 defines `دخول سحب أسبوعي تلقائي` ("automatic weekly draw entry") under onboarding, while §13.2 defines Lucky Wheel separately under UI/Engagement; separated in `BE-DRW-01` |
| UI visibility condition ("appears only when eligible") | **YES (SOURCE-CONFIRMED)** | Arabic PDF §13.2: `أيقونة صغيرة يسار الشاشة` ("small icon on the left of the screen"), `تظهر فقط عند توافر الشروط` ("appears only when conditions are met"); Badges Report C-09: `Should display only when eligible` |
| Admin manages wheel contents & conditions | **YES (SOURCE-CONFIRMED at high level)** | Arabic PDF §11.4 (`إدارة العروض والمحتوى`): `إدارة محتويات العجلة (كما هو موضح)` ("admin management of wheel contents"); Badges Report C-09: `Trigger: Admin-defined condition is met` |
| Generic outcome categories (`points / discount / prize`) | **YES (SOURCE-CONFIRMED)** | Badges Report C-09: `Reward: Random points/discount/prize` |
| Concrete `LuckyWheelPrizeType` enum (`pp_coins`, `discount_offer`, `cashback_offer`, `badge`, `no_prize`) | **NO (CURRENT IMPLEMENTATION / TECHNICAL DECISION)** | Concrete backend enum choices in `apps.lucky_wheel.models.LuckyWheelPrizeType`. While `points` and `discount` map to C-09's generic `Random points/discount/prize`, `cashback_offer`, `badge`, and `no_prize` are concrete repository implementation choices rather than explicit product-spec requirements |
| `AUCTION_COMPLETED` trigger -> winning buyer receives `1` attempt | **NO (CURRENT IMPLEMENTATION / TECHNICAL DECISION)** | Badges Report C-09 states `Purpose: Supports auction excitement` and `Trigger: Admin-defined condition is met`, but does **not** explicitly mandate the completed-auction trigger, buyer-only beneficiary, or `1`-attempt count. `LuckyWheelGrantService.grant_for_auction_completed` granting `1` attempt to the winning buyer `CustomerProfile` on an auction `CompletedDeal` is a current implementation / technical decision |
| `MANAGER_GRANT` source type with `N` attempts | **NO (CURRENT IMPLEMENTATION / TECHNICAL DECISION)** | High-level admin condition/content control is source-confirmed, but `LuckyWheelGrantSourceType.MANAGER_GRANT` with `attempts >= 1` is a concrete backend implementation representation |
| Attempt expiration (`expires_at`) & consumption order (`expires_at ASC NULLS LAST, created ASC, id ASC`) | **NO (CURRENT IMPLEMENTATION / TECHNICAL DECISION)** | Nullable `LuckyWheelGrant.expires_at`; active grants exclude `expires_at <= now` and consume expiring grants before non-expiring grants (`F("expires_at").asc(nulls_last=True), "created", "id"`, `BE-LW-01A`) |
| Cooldown / frequency | **NO (UNRESOLVED)** | No cooldown duration is specified in the Arabic PDF or Badges Report; `cooldown_until` is exposed as `None` |
| Number of wheel slots | **NO (UNRESOLVED)** | Diagram in §13.2 shows illustrative numbers (`1, 2, 5, 10`), not a mandatory 8-slot rule; backend does not hardcode a fixed slot count |
| Prize validity | **YES (SOURCE-CONFIRMED)** | Badges Report C-09: `Prize validity set per wheel item; default 7 days for discounts` |
| Weighting / probability (`LuckyWheelPrize.weight`) | **NO (CURRENT IMPLEMENTATION / TECHNICAL DECISION; audited in BE-LW-02)** | Positive integer `LuckyWheelPrize.weight` (`>= 1`) with weighted selection is a current implementation choice, not a source-confirmed probability rule; spin randomness, weights, fairness, inventory races, and prize application will be audited in `BE-LW-02` |

### Domain Separation: Weekly Onboarding Draw != Lucky Wheel

- Fresh customer registration (`POST /api/auth/register/customer/`), seller profile creation (`POST /api/users/mobile/sellers/`), customer profile creation (`POST /api/users/mobile/customers/`), and profile switching (`POST /api/auth/switch-profile/`) create **0** `LuckyWheelGrant` rows.
- Onboarding grants an account-level `WeeklyDrawEntry` in `apps.draws` (`BE-DRW-01`), not a Lucky Wheel attempt.
- Historical `LuckyWheelGrant(source_type="onboarding")` rows and `LuckyWheelGrantService.grant_onboarding_attempt()` remain preserved for backward compatibility and historical audit.

### Hardened Eligibility, Attempt Lifecycle, Savepoint Recovery & Public API Whitelisting (`apps/lucky_wheel`)

- **Canonical Availability Service (`LuckyWheelService.get_availability`):**
  - Read-only evaluation returning `available`, `eligible`, `has_active_wheel`, `remaining_attempts`, `cooldown_until`, `segments`, and `active_grants`.
  - `available_prizes()` filters `is_active=True`, `deleted__isnull=True`, and `quantity_total__isnull=True | quantity_awarded < quantity_total` ordered by `id`.
  - `available` and `eligible` are `True` iff `has_active_wheel` (at least one active, non-exhausted prize segment exists) **AND** `remaining_attempts > 0` across active, non-deleted, non-expired (`expires_at IS NULL OR expires_at > now`) grants scoped to `(user, profile)`.
  - Repeated calls to `GET /api/lucky-wheel/current/` and `LuckyWheelService.get_availability()` perform **0** database writes, **0** `PointTransaction` mutations, and **0** notifications.
- **Deterministic Cross-Engine Grant Ordering (`LuckyWheelGrantService.active_grants`, BE-LW-01A):**
  - Orders usable grants by `F("expires_at").asc(nulls_last=True), "created", "id"` (`expires_at ASC NULLS LAST, created ASC, id ASC`).
  - Expiring grants are always consumed before non-expiring grants (`expires_at=None`) identically across PostgreSQL and SQLite, with earlier `expires_at` first and `("created", "id")` as the deterministic tie-breaker.
- **Savepoint-Safe Grant Creation & Collision Validation (`LuckyWheelGrantService.grant_attempt`, BE-LW-01 & BE-LW-01A):**
  - Runs in `transaction.atomic()`, locks `UserAccount` with `select_for_update()`, rejects boolean/non-positive `attempts`, and rejects `ManagerProfile` recipients.
  - Wraps `LuckyWheelGrant.objects.create(...)` in an inner `with transaction.atomic():` savepoint so a concurrent unique-key `IntegrityError` (or `full_clean()` uniqueness `ValidationError`) rolls back only the inner savepoint without poisoning the outer transaction (`TransactionManagementError` prevented).
  - Both the normal pre-check path and the post-`IntegrityError` fallback path validate `_validate_existing_grant_match` (`existing.user_id == profile.user_id`, `existing.source_type == source_type`, `existing.source_id == source_id_str`, and matching `profile_id` when set), raising `400 ValidationError` on any cross-user or cross-source collision.
  - Granting an attempt creates **0** `PointTransaction` rows, **0** `UserPromotionGrant` rows, **0** `BadgeAward` rows, **0** `LuckyWheelSpin` rows, and **0** notifications.
- **Atomic Attempt Consumption & Cross-User Spin Isolation (`LuckyWheelSpinService.spin`):**
  - Runs in `transaction.atomic()` and locks `UserAccount` with `select_for_update()` to serialize concurrent spin requests for the same user.
  - Same-user retry with an existing `idempotency_key` returns the stored `LuckyWheelSpin` without decrementing another attempt or re-awarding prizes; cross-user reuse of another user's `idempotency_key` is rejected with `400 ValidationError`.
  - Locks the first usable `LuckyWheelGrant` row (ordered by `expires_at ASC NULLS LAST, created ASC, id ASC`) with `select_for_update()`, re-verifies `is_active=True`, `deleted is None`, `remaining_attempts > 0`, and `expires_at > now`, decrements `remaining_attempts` by 1, and sets `is_active=False` when `remaining_attempts` reaches `0`.
- **Public API Field Whitelisting & Client Input Security (`apps/lucky_wheel/serializers.py`, BE-LW-01 & BE-LW-01A):**
  - `LuckyWheelSegmentSerializer` (`GET /api/lucky-wheel/current/` -> `segments`) exposes only public display fields: `id`, `label`, `description`, `prize_type`, `points_amount`, and `badge_type`. It does **not** expose `weight`, `quantity_total`, `quantity_awarded`, `is_active`, `discount_offer`, `cashback_offer`, or internal `metadata`.
  - `LuckyWheelGrantSerializer` (`GET /api/lucky-wheel/current/` -> `active_grants`) exposes only safe public grant fields: `id`, `source_type`, `total_attempts`, `remaining_attempts`, `expires_at`, `is_active`, and `created`. It does **not** expose `idempotency_key`, `source_id`, or internal `metadata`.
  - `LuckyWheelSpinSerializer` (`GET /api/lucky-wheel/spins/my/`) omits internal `metadata` while `AdminLuckyWheelSpinSerializer` (`/api/lucky-wheel/admin/spins/`) and `LuckyWheelPrizeSerializer` (`/api/lucky-wheel/admin/prizes/`) retain full manager configuration and audit fields.
  - `LuckyWheelSpinRequestSerializer` accepts only `idempotency_key` and rejects any client-supplied `prize_id`, `prize`, `outcome`, `slot`, `grant_id`, `remaining_attempts`, `eligible`, or `available` field with `400 ValidationError`.
  - `LuckyWheelPrizeSerializer.validate()` runs model `clean()` and translates `django.core.exceptions.ValidationError` into DRF `serializers.ValidationError` (`400 Bad Request`).

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/lucky_wheel/services.py` | Added `LuckyWheelService` (`available_prizes`, `get_availability`); hardened `LuckyWheelGrantService` (inner `transaction.atomic()` savepoint around `create()`, `_validate_existing_grant_match` on both normal and `IntegrityError` fallback paths, `F("expires_at").asc(nulls_last=True)` ordering) and `LuckyWheelSpinService` (`UserAccount` + `LuckyWheelGrant` `select_for_update()`, cross-user idempotency check) | Canonical eligibility truth, savepoint-safe concurrency, deterministic cross-DB grant ordering, and cross-user isolation |
| `apps/lucky_wheel/serializers.py` | Added `available` and `has_active_wheel` to `LuckyWheelCurrentSerializer`; removed internal `metadata` and `source_id` from public `LuckyWheelSegmentSerializer`, `LuckyWheelGrantSerializer`, and `LuckyWheelSpinSerializer`; rejected extra client fields on `LuckyWheelSpinRequestSerializer`; wired `instance.clean()` into `LuckyWheelPrizeSerializer.validate()` | Public API data hygiene, client input hardening, and `400` admin validation |
| `apps/lucky_wheel/views.py` | Delegated `LuckyWheelCurrentViewSet.list` to `LuckyWheelService.get_availability` and serialized `active_grants` via `LuckyWheelGrantSerializer` | Keep view thin and enforce public grant field whitelist |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | Added Arabic translations for new Lucky Wheel validation messages | Arabic-first localization |
| `tests/lucky_wheel/test_lucky_wheel_eligibility_lifecycle.py` | New, 57 tests (`test_01` through `test_44` + `test_lw01a_*` + parameterized cases) | Full BE-LW-01 & BE-LW-01A coverage |

### Test results (2026-10-06, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/lucky_wheel/test_lucky_wheel_eligibility_lifecycle.py -q` | 57 passed |
| `pytest tests/lucky_wheel tests/rewards_sprint tests/draws tests/promotions tests/seasons tests/referrals tests/loyalty/test_deal_completed_rewards.py tests/loyalty/test_account_onboarding_reward.py tests/test_api_docs.py tests/test_i18n.py -q` | 614 passed |
| `python manage.py makemigrations --check --dry-run` | No changes detected |

## 24. Lucky Wheel Spin Outcome + Prize Execution Hardening (BE-LW-02)

Status: **DONE**

### Source-of-Truth Verification & Provenance Classification (`BE-LW-02`)

Audited sources:
1. Original Arabic product specification PDF (`App pigeon planet وثيقة نطاق المشروع ومواصفات التطبيق النهائية2.pdf`, Section 11.4 line 1210, Section 13.2 lines 1408–1418)
2. Badges & Reward Scenarios Report (`Pigeon_Planet_Badges_and_Reward_Scenarios_Report.pdf`, Table C-09)
3. `docs/rewards/REWARDS_SOURCE_OF_TRUTH.md`, `docs/rewards/rewarding-system-report.md`, and `AGENTS.md`

| Rule / Requirement | Source-Confirmed? | Provenance Category & `BE-LW-02` Implementation |
| --- | --- | --- |
| Lucky Wheel generic outcome categories (`points / discount / prize`) | **YES (SOURCE-CONFIRMED)** | Badges Report Scenario `C-09`: `Reward: Random points/discount/prize` |
| Admin manages wheel contents | **YES (SOURCE-CONFIRMED)** | Arabic PDF §11.4 (`إدارة محتويات العجلة (كما هو موضح)`) |
| Concrete `LuckyWheelPrizeType` enum (`pp_coins`, `discount_offer`, `cashback_offer`, `badge`, `no_prize`) | **NO (CURRENT IMPLEMENTATION / TECHNICAL DECISION)** | Concrete backend representation in `apps.lucky_wheel.models.LuckyWheelPrizeType`; preserved with strict one-of payload validation per `prize_type` |
| Positive integer weighting (`LuckyWheelPrize.weight >= 1`) & RNG selection | **NO (CURRENT IMPLEMENTATION / TECHNICAL DECISION)** | Neither PDF specifies fixed slot counts or fixed probability percentages; positive integer `weight >= 1` is preserved and executed via `LuckyWheelPrizeSelector.select()` using `secrets.SystemRandom()` |
| Finite inventory (`quantity_total` / `quantity_awarded`) | **NO (CURRENT IMPLEMENTATION / TECHNICAL DECISION)** | Enforced at both DB (`wheel_prize_awarded_lte_total_ck`) and service level (`select_for_update()` row lock + bounded stale-candidate re-resolution) |
| Discount / offer validity (`Prize validity set per wheel item; default 7 days for discounts`) | **PARTIAL (SECONDARY REPORT ONLY; NOT IN PRIMARY ARABIC PDF)** | Secondary report `Pigeon_Planet_Badges_and_Reward_Scenarios_Report.pdf` Table `C-09` notes `"Prize validity set per wheel item; default 7 days for discounts"`, whereas the primary Arabic specification PDF does not define a 7-day rule and `LuckyWheelPrize` has no dedicated validity column. To support per-item validity without schema changes or breaking existing contracts, `LuckyWheelSpinService._resolve_grant_expires_at()` sets `UserPromotionGrant.expires_at = awarded_at + timedelta(days=validity_days)` when `prize.metadata["validity_days"]` is a positive integer, and preserves `expires_at = None` when omitted |

### Hardened Randomness, Candidate Selection, Concurrent Inventory & Atomic Prize Execution (`apps/lucky_wheel`)

1. **Randomness Engine & Injectable Selector (`LuckyWheelPrizeSelector.select`):**
   - Replaces module-global `random.randint(...)` with `_SYSTEM_RANDOM = secrets.SystemRandom()`.
   - `LuckyWheelPrizeSelector.select(candidates, *, rng=None)` filters candidates to `is_active=True`, `deleted is None`, `weight > 0`, and `quantity_total is None or quantity_awarded < quantity_total`, sums positive integer weights, draws a 1-based pick in `[1, total_weight]` via `secrets.SystemRandom().randint(1, total_weight)` (or an injected `rng` in tests), and traverses cumulative weights in deterministic `id` ascending order.
2. **Candidate Set & Concurrent Finite Inventory Hardening (`LuckyWheelService.available_prizes` & `LuckyWheelSpinService._choose_prize`):**
   - `LuckyWheelService.available_prizes()` filters `is_active=True`, `deleted__isnull=True`, `weight__gt=0`, and `quantity_total__isnull=True | quantity_awarded__lt=F("quantity_total")` ordered by `id` with `select_related("discount_offer", "cashback_offer")`.
   - `LuckyWheelSpinService._choose_prize(rng=None)` selects a candidate via `LuckyWheelPrizeSelector.select()`, locks the `LuckyWheelPrize` row with `select_for_update()`, and re-verifies `is_active`, `deleted is None`, `weight > 0`, and `quantity_awarded < quantity_total` under the lock.
   - If a selected finite prize was concurrently exhausted or deactivated between the candidate query and `select_for_update()`, `_choose_prize()` adds its `pk` to `excluded_ids` and boundedly re-resolves from the remaining valid candidates. If no valid candidates remain, it raises `400 ValidationError` and rolls back the spin without over-awarding inventory or consuming the user's attempt.
3. **Transaction Ordering & Full Atomic Rollback (`LuckyWheelSpinService.spin`):**
   - Inside `transaction.atomic()`:
     1. Validates `idempotency_key` and `profile.user_id == user.pk`.
     2. Locks `UserAccount` with `select_for_update()`.
     3. Checks existing `LuckyWheelSpin` by `idempotency_key` (returns existing spin idempotently for the same user; raises `400 ValidationError` for a different user).
     4. Locks the first usable `LuckyWheelGrant` (`expires_at ASC NULLS LAST, created ASC, id ASC`) with `select_for_update()` and re-verifies `is_active`, `deleted is None`, `remaining_attempts > 0`, and `expires_at > now`.
     5. Selects and locks a valid `LuckyWheelPrize`, validates its target payload/offer (`_validate_prize_target`), and increments `prize.quantity_awarded += 1`.
     6. Decrements `grant.remaining_attempts -= 1` (and sets `grant.is_active = False` at `0`).
     7. Creates `LuckyWheelSpin` with immutable `prize_snapshot`.
     8. Executes `_apply_prize(spin, prize)` (`PointService.earn_points`, `UserPromotionGrantService.grant_discount`, `UserPromotionGrantService.grant_cashback`, `BadgeService.award_badge`, or `None` for `NO_PRIZE`).
   - Any failure in prize selection, target validation, spin creation, or reward application rolls back `grant.remaining_attempts`, `prize.quantity_awarded`, `LuckyWheelSpin`, and all reward side effects atomically.
4. **Strict Admin Prize Configuration & Spin-Time Target Validation (`LuckyWheelPrize.clean` & `_validate_prize_target`):**
   - `LuckyWheelPrize.clean()` enforces positive `weight > 0`, `quantity_total > 0` when specified, `quantity_total >= quantity_awarded` (rejecting admin attempts to lower `quantity_total` below `quantity_awarded` with `400`), optional positive integer `metadata["validity_days"]`, and strict one-of reward payload fields per `prize_type` (`PP_COINS` -> only `points_amount > 0`; `DISCOUNT_OFFER` -> only active `lucky_wheel` `discount_offer`; `CASHBACK_OFFER` -> only active `lucky_wheel` `cashback_offer`; `BADGE` -> only valid `badge_type`; `NO_PRIZE` -> no reward fields).
   - At spin time, `_validate_prize_target()` re-verifies that a `DISCOUNT_OFFER` or `CASHBACK_OFFER` prize's linked offer is still `is_active=True`, `deleted is None`, and `trigger_type == OfferTriggerType.LUCKY_WHEEL`, rejecting and rolling back the spin with `400 ValidationError` if the offer was deactivated or soft-deleted after prize creation.
5. **Historical Snapshot Immutability (`LuckyWheelSpin._snapshot_prize`):**
   - `prize_snapshot` captures `id`, `label`, `description`, `prize_type`, `points_amount`, `discount_offer_id`, `discount_offer` (`id`, `title`, `value_type`, `value`), `cashback_offer_id`, `cashback_offer` (`id`, `title`, `value_type`, `value`), and `badge_type`, remaining untouched by later manager edits or prize deactivation.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/lucky_wheel/models.py` | Hardened `LuckyWheelPrize.clean()` with positive weight check, strict one-of reward fields per `prize_type`, active/non-deleted offer check, `quantity_total >= quantity_awarded`, and optional `metadata["validity_days"]` validation | Prevent invalid/mixed prize configurations and illegal inventory cap reductions |
| `apps/lucky_wheel/services.py` | Added `LuckyWheelPrizeSelector` (`secrets.SystemRandom()` + injectable `rng`); hardened `LuckyWheelService.available_prizes()` (`weight__gt=0` + `select_related`); hardened `LuckyWheelSpinService` (`_choose_prize` bounded stale-inventory re-resolution, `_validate_prize_target` spin-time offer/payload checks, `_resolve_grant_expires_at`, enriched `_snapshot_prize`) | Cryptographic weighted selection, concurrency-safe finite inventory, atomic rollback on target/service failures, and immutable snapshots |
| `apps/lucky_wheel/views.py` | Guarded `profile is not None and profile.type == ProfileType.CUSTOMER` when building `balances` in `LuckyWheelSpinViewSet.create` | Safe balance lookup across profile modes |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | Added 30 Arabic translations and compiled `.mo` | Arabic-first localization |
| `tests/lucky_wheel/test_lucky_wheel_spin_execution.py` | New, 72 tests (`test_01` through `test_60` + parameterized & strict one-of / validity tests) | Full BE-LW-02 coverage |

### Test results (2026-10-06, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/lucky_wheel/test_lucky_wheel_spin_execution.py -q` | 72 passed |
| `pytest tests/lucky_wheel/test_lucky_wheel_eligibility_lifecycle.py -q` | 57 passed |
| `pytest tests/lucky_wheel tests/rewards_sprint tests/draws tests/promotions tests/seasons tests/referrals tests/loyalty/test_deal_completed_rewards.py tests/loyalty/test_account_onboarding_reward.py tests/test_api_docs.py tests/test_i18n.py -q` | 686 passed |
| `python manage.py makemigrations --check --dry-run` | No changes detected |

## 25. Shared Prize Award / Fulfillment Foundation (BE-PRZ-01)

Status: **DONE**

### Requirement & Scope Boundary

Implement the shared prize-award and fulfillment foundation (`apps.prizes`: `PrizeAward`, `PrizeAwardService`, user read-only APIs, and manager monitoring APIs) that records canonical prize wins across integrated reward sources (`LUCKY_WHEEL`, `SEASON`, `REFERRAL_CHALLENGE`) and distinguishes:
- **`DIGITAL_AUTOMATIC`**: digital prizes executed automatically inside the system (`status = FULFILLED`, `fulfilled_at` set, no claim form required).
- **`CLAIM_REQUIRED`**: non-digital / in-kind prizes that require winner claim submission and manager fulfillment (`status = PENDING`, `fulfilled_at = None`).

**Scope Boundary:**
- `BE-PRZ-01` owns `PrizeAward` creation, source integrations, idempotency, immutability, user read-only endpoints (`GET /api/prizes/my-awards/`, `GET /api/prizes/my-awards/<id>/`), and manager monitoring endpoints (`GET /api/prizes/admin/awards/`, `GET /api/prizes/admin/awards/<id>/`).
- `BE-PRZ-01` does **not** implement winner claim submission (`name`, `address`, `identity_number` PII), manager claim review, delivery/fulfillment transitions (`CLAIMED`, `APPROVED`, `DELIVERED`, `REJECTED`), or claim notifications — those belong to **`BE-PRZ-02`** and **`BE-NOT-*`**.

### Existing Prize Domain Audit & Classification Table

| Source Domain | Model / Service | Prize Types | Classification in `BE-PRZ-01` | Notes |
| --- | --- | --- | --- | --- |
| **Lucky Wheel** (`apps.lucky_wheel`) | `LuckyWheelSpin` / `LuckyWheelSpinService.spin()` | `PP_COINS`, `DISCOUNT_OFFER`, `CASHBACK_OFFER`, `BADGE` | `DIGITAL_AUTOMATIC` / `FULFILLED` | Digital reward is applied first via `_apply_prize()`, then `PrizeAwardService.record_digital_fulfilled()` records the unified ledger row in the same atomic transaction (`idempotency_key = "prize:source:lucky_wheel:spin:{spin.pk}"`). `NO_PRIZE` creates `0` `PrizeAward` rows. |
| **Seller Seasons** (`apps.seasons`) | `SeasonSellerInsight` + `SeasonPrize` / `SeasonPayoutService.payout_season_winners()` | Top-3 `SeasonPrize.pp_points > 0` (`100,000 / 50,000 / 25,000 PP` default) | `DIGITAL_AUTOMATIC` / `FULFILLED` | PP payout is applied first via `PointService.earn_points()`, then `PrizeAwardService.record_digital_fulfilled()` records one `PrizeAward` per newly paid Top-3 winner (`source_id = str(insight.pk)`, `idempotency_key = "prize:source:season:{season.pk}:seller:{seller.pk}"`). Optional descriptive `symbolic_prize_code` / `title` strings on `SeasonPrize` are not free-text parsed into physical claims; zero-PP season prizes create `0` `PrizeAward` rows, and historical pre-`BE-PRZ-01` paid seasons are not backfilled. |
| **Monthly Referral Challenge** (`apps.referrals`) | `ReferralChallengeWinner` / `ReferralChallengeService.finalize()` | `ReferralChallengePrizeCode.IPHONE` (Rank 1), `ReferralChallengePrizeCode.AIRPODS` (Rank 2) | `CLAIM_REQUIRED` / `PENDING` | Recorded atomically during `ReferralChallengeService.finalize()` via `PrizeAwardService.record_claim_required()` (`source_id = str(winner.pk)`, `idempotency_key = "prize:source:referral_challenge:winner:{winner.pk}"`, `fulfilled_at = None`, zero PII stored). |
| **Monthly Referral Challenge Rank 3** (`apps.referrals`) | `ReferralChallengeWinner` (`ANNUAL_LOFT_SUBSCRIPTION`) | `ReferralChallengePrizeCode.ANNUAL_LOFT_SUBSCRIPTION` | **Deferred / Unresolved Fulfillment Provisioning** | `ANNUAL_LOFT_SUBSCRIPTION` is a digital subscription concept in `ReferralChallengePrizeCode`, but `apps.referrals` does not link a `PedigreeTemplatePackage` SKU or provision a `LoftSubscription` on challenge finalization. Per `BE-PRZ-01` rules, it is neither falsely recorded as `DIGITAL_AUTOMATIC` / `FULFILLED` nor misclassified as a physical shipment `CLAIM_REQUIRED` prize. |
| **Weekly Onboarding Draw** (`apps.draws`) | `WeeklyDraw` / `WeeklyDrawEntry` | Unspecified (`BE-DRW-02` blocked) | **Excluded (`BE-DRW-02` blocked)** | Weekly draw winner count, selection algorithm, and prize definition remain blocked in `BE-DRW-02`; `WEEKLY_DRAW` is not in `PrizeAwardSourceType` and `WeeklyDrawService.finalize_draw()` creates `0` `PrizeAward` rows. |

### Domain Model & Invariants (`apps/prizes/models.py`)

- `PrizeAwardSourceType(models.TextChoices)`: `SEASON`, `REFERRAL_CHALLENGE`, `LUCKY_WHEEL`
- `PrizeAwardFulfillmentType(models.TextChoices)`: `DIGITAL_AUTOMATIC`, `CLAIM_REQUIRED`
- `PrizeAwardStatus(models.TextChoices)`: `PENDING`, `FULFILLED`
- `PrizeAward(BaseModel)`:
  - `user` (`ForeignKey("authentication.UserAccount", on_delete=PROTECT, related_name="prize_awards")`) — canonical account-level owner across Customer/Seller profile switches
  - `profile` (`ForeignKey("users.Profile", on_delete=PROTECT, null=True, blank=True, related_name="prize_awards")`) — optional context-only profile (`profile.user_id == user_id` enforced)
  - `source_type` (`CharField`, `choices=PrizeAwardSourceType.choices`)
  - `source_id` (`CharField(max_length=100)`)
  - `fulfillment_type` (`CharField`, `choices=PrizeAwardFulfillmentType.choices`)
  - `status` (`CharField`, `choices=PrizeAwardStatus.choices`)
  - `title` (`CharField(max_length=200)`)
  - `description` (`TextField(blank=True, default="")`)
  - `prize_snapshot` (`JSONField(default=dict, blank=True)`) — immutable JSON snapshot of the awarded prize
  - `idempotency_key` (`CharField(max_length=180, unique=True)`)
  - `awarded_at` (`DateTimeField(default=timezone.now)`)
  - `fulfilled_at` (`DateTimeField(null=True, blank=True)`)
  - `metadata` (`JSONField(default=dict, blank=True)`)
  - DB CheckConstraint `prizes_award_status_fulfillment_consistency_ck`:
    - `(fulfillment_type = 'DIGITAL_AUTOMATIC' AND status = 'FULFILLED' AND fulfilled_at IS NOT NULL)` **OR** `(fulfillment_type = 'CLAIM_REQUIRED' AND ((status = 'PENDING' AND fulfilled_at IS NULL) OR (status = 'FULFILLED' AND fulfilled_at IS NOT NULL)))`
  - DB Indexes: `prizes_award_user_created_idx`, `prizes_award_source_idx`, `prizes_award_status_type_idx`
  - **Immutability & Delete Protection:** Once created, `user_id`, `profile_id`, `source_type`, `source_id`, `fulfillment_type`, `status`, `title`, `description`, `prize_snapshot`, `idempotency_key`, `awarded_at`, and `fulfilled_at` cannot be mutated, and `delete()` raises `ProtectedError`.
  - **No PII Guard:** `FORBIDDEN_PRIZE_PII_KEYS` (`name`, `full_name`, `recipient_name`, `claimant_name`, `address`, `shipping_address`, `delivery_address`, `street_address`, `national_id`, `identity_number`, `id_number`, `passport_number`, `phone`, `phone_number`) are rejected in both `prize_snapshot` and `metadata`.

### Service Layer (`apps/prizes/services.py`)

- `PrizeAwardService.record_digital_fulfilled(...)`:
  - Records a `DIGITAL_AUTOMATIC` / `FULFILLED` `PrizeAward` with `fulfilled_at` set.
  - Pure ledger/audit recording: does **not** call `PointService`, `UserPromotionGrantService`, or `BadgeService` itself, preventing any double-granting when called by integrated source services.
- `PrizeAwardService.record_claim_required(...)`:
  - Records a `CLAIM_REQUIRED` / `PENDING` `PrizeAward` with `fulfilled_at = None`.
  - Creates zero `PointTransaction`, zero `UserPromotionGrant`, zero `BadgeAward`, zero `PaymentRequest`, and zero `Notification` rows.
- **Idempotency & Savepoint-Safe Concurrency:**
  - Locks `UserAccount` with `select_for_update()` inside `transaction.atomic()`, checks existing `PrizeAward` by `idempotency_key`, and wraps `PrizeAward.objects.create(...)` in an inner `with transaction.atomic():` savepoint to recover cleanly from concurrent unique-key `IntegrityError` without poisoning the outer transaction.
  - Both normal pre-check and `IntegrityError` recovery paths run `_validate_existing_award_match()`, rejecting any cross-user, cross-profile, cross-source, or cross-fulfillment-type collision with `400 ValidationError`.

### APIs & Permissions (`apps/prizes/views.py`, `apps/prizes/urls.py`, `apps/users/manager_permissions.py`)

| Method | Path | Access / Permission | Behavior |
| --- | --- | --- | --- |
| `GET` | `/api/prizes/my-awards/` | `IsAuthenticated` | Lists authenticated `request.user`'s own `PrizeAward` rows across Customer/Seller modes ordered by `-awarded_at, -id`; hides `idempotency_key` and internal `metadata` |
| `GET` | `/api/prizes/my-awards/<id>/` | `IsAuthenticated` | Retrieves one `PrizeAward` owned by `request.user` (`404` for another user's award); `POST`/`PATCH`/`PUT`/`DELETE` return `405` |
| `GET` | `/api/prizes/admin/awards/` | `prizes.awards.list` | Manager read-only list of all `PrizeAward` rows with `status`, `fulfillment_type`, `source_type`, and `user` filters |
| `GET` | `/api/prizes/admin/awards/<id>/` | `prizes.awards.retrieve` | Manager read-only detail of one `PrizeAward` row; `POST`/`PATCH`/`PUT`/`DELETE` return `405` |

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/prizes/__init__.py`, `apps.py`, `models.py`, `services.py`, `serializers.py`, `views.py`, `urls.py`, `admin.py`, `migrations/0001_initial.py` | New `apps.prizes` app, `PrizeAward` model/constraints, `PrizeAwardService`, user & manager read-only APIs, and schema migration | Shared prize award / fulfillment foundation |
| `ppw/settings/base.py`, `ppw/urls.py` | Registered `"apps.prizes"` and mounted `/api/prizes/` | App and route wiring |
| `apps/users/manager_permissions.py` | Registered `prizes.awards.list` and `prizes.awards.retrieve` | Manager RBAC |
| `apps/lucky_wheel/services.py` | Wired `LuckyWheelSpinService._record_prize_award()` into `spin()` after `_apply_prize()` for non-`NO_PRIZE` outcomes | Record `DIGITAL_AUTOMATIC` / `FULFILLED` `PrizeAward` on winning wheel spins |
| `apps/seasons/services.py` | Wired `SeasonPayoutService._record_prize_award()` into `payout_season_winners()` after `PointService.earn_points()` | Record `DIGITAL_AUTOMATIC` / `FULFILLED` `PrizeAward` for Top-3 season PP winners |
| `apps/referrals/services.py` | Wired `ReferralChallengeService._record_winner_prize_awards()` into `finalize()` for `IPHONE` and `AIRPODS` winners | Record `CLAIM_REQUIRED` / `PENDING` `PrizeAward` for canonical in-kind referral challenge winners |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | Added 35 Arabic translations and compiled `.mo` | Arabic-first localization |
| `tests/prizes/test_prize_awards.py` | New, 63 tests (`test_01` through `test_63`) | Full BE-PRZ-01 coverage |

### Test results (2026-10-06, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/prizes/test_prize_awards.py -q` | 63 passed |
| `pytest tests/prizes tests/lucky_wheel tests/seasons tests/referrals tests/draws tests/promotions tests/rewards_sprint tests/loyalty/test_deal_completed_rewards.py tests/loyalty/test_account_onboarding_reward.py tests/users/test_manager_permissions.py tests/test_api_docs.py tests/test_i18n.py -q` | 780 passed |
| `python manage.py makemigrations --check --dry-run` | No changes detected |
| Scratch SQLite DB migration (`migrate` -> `migrate prizes zero` -> `migrate prizes 0001`) | Succeeded with `0` backfilled rows |

## 26. Claim-Required Prize Submission, Manager Review & Delivery (BE-PRZ-02)

Status: **DONE**

### Requirement & Scope Boundary

Implement the claim submission and manager review/delivery workflow for `CLAIM_REQUIRED` `PrizeAward` rows (`PrizeClaim`, `PrizeClaimService`, user claim submission API, and manager claim list/retrieve/review/deliver APIs) matching the canonical product flow:
1. Winner of a `CLAIM_REQUIRED` prize submits `full_name`, `address`, and `identity_number` -> `PrizeClaim` created as `SUBMITTED`, while `PrizeAward.status` remains `PENDING` (`fulfilled_at = None`).
2. Manager reviews the submitted claim -> `PrizeClaim` transitions `SUBMITTED -> REVIEWED` (`reviewed_at`, `reviewed_by` recorded), while `PrizeAward.status` remains `PENDING` (`fulfilled_at = None`).
3. Manager marks the reviewed claim delivered -> `PrizeClaim` transitions `REVIEWED -> DELIVERED` (`delivered_at`, `delivered_by` recorded) and `PrizeAward` transitions `PENDING -> FULFILLED` (`fulfilled_at = delivered_at`) atomically.

**Out of Scope (Simplicity Wins):**
- No rejection/appeal/resubmission workflows, file/document uploads, shipping carrier/tracking integration, bank/IBAN/payment fields, notifications (`BE-NOT-*`), weekly-draw winner selection (`BE-DRW-02`), or annual Loft subscription provisioning.

### PII Storage & Isolation Audit

- Audited `apps/users/models.py`: `Profile.national_id` (`CharField(max_length=50, null=True, blank=True)`) and `Profile.address` (`TextField(blank=True)`) use standard Django database fields without a custom encryption framework, relying on database storage security and strict serializer/endpoint access control.
- `PrizeClaim` follows the exact repository convention (`full_name = CharField(max_length=255)`, `address = TextField()`, `identity_number = CharField(max_length=100)`), never hashes `identity_number` (since managers must inspect it during review), never copies PII into `PrizeAward.prize_snapshot` or `PrizeAward.metadata`, never echoes `identity_number` or `address` in user-facing responses (`POST /api/prizes/my-awards/<id>/claim/`, `GET /api/prizes/my-awards/`, `GET /api/prizes/my-awards/<id>/`), and exposes claimant PII exclusively on manager-authorized claim endpoints (`prizes.claims.*`).

### Domain Model & Invariants (`apps/prizes/models.py`)

- `PrizeClaimStatus(models.TextChoices)`: `SUBMITTED`, `REVIEWED`, `DELIVERED`
- `PrizeClaim(BaseModel)`:
  - `award` (`OneToOneField(PrizeAward, on_delete=PROTECT, related_name="claim")`) — enforces at most one claim per `PrizeAward`
  - `full_name` (`CharField(max_length=255)`)
  - `address` (`TextField()`)
  - `identity_number` (`CharField(max_length=100)`)
  - `status` (`CharField(max_length=20, choices=PrizeClaimStatus.choices, default=SUBMITTED, db_index=True)`)
  - `submitted_at` (`DateTimeField(db_index=True)`)
  - `reviewed_at` (`DateTimeField(null=True, blank=True)`)
  - `reviewed_by` (`ForeignKey("authentication.UserAccount", on_delete=PROTECT, null=True, blank=True, related_name="reviewed_prize_claims")`)
  - `delivered_at` (`DateTimeField(null=True, blank=True)`)
  - `delivered_by` (`ForeignKey("authentication.UserAccount", on_delete=PROTECT, null=True, blank=True, related_name="delivered_prize_claims")`)
  - DB Index: `prize_claim_status_sub_idx` on `("status", "-submitted_at", "-id")`
  - **Immutability & Delete Protection:** `award_id`, `full_name`, `address`, `identity_number`, and `submitted_at` cannot be modified after creation; `SUBMITTED -> DELIVERED` (skipping review) is rejected; `REVIEWED` and `DELIVERED` claims lock `reviewed_at` and `reviewed_by_id`; `DELIVERED` is terminal; `delete()` raises `ProtectedError`.
  - **`PrizeAward` Fulfillment Guard:** `PrizeAward.clean()` requires a `CLAIM_REQUIRED` award to start in `PENDING` status and permits `status = FULFILLED` on update only when a linked `PrizeClaim` exists with `status = DELIVERED`.

### Service Layer (`apps/prizes/services.py`)

- `PrizeClaimService.submit_claim(user, award_id, *, full_name, address, identity_number)`:
  - Validates non-blank trimmed `full_name`, `address`, and `identity_number`.
  - Locks `PrizeAward` with `select_for_update()` inside `transaction.atomic()`.
  - Verifies `award.user_id == user.pk` (`404` otherwise), `award.fulfillment_type == CLAIM_REQUIRED`, no existing `PrizeClaim`, and `award.status == PENDING`.
  - Creates `PrizeClaim(status=SUBMITTED, submitted_at=timezone.now())` inside a savepoint while leaving `PrizeAward.status = PENDING` and `fulfilled_at = None`.
- `PrizeClaimService.review_claim(claim_id, reviewer_user)`:
  - Locks `PrizeClaim` and `PrizeAward` with `select_for_update()` inside `transaction.atomic()`.
  - Idempotent when already `REVIEWED` (returns existing claim without overwriting `reviewed_at` or `reviewed_by`).
  - Rejects `DELIVERED` claims with `400 ValidationError`.
  - Transitions `SUBMITTED -> REVIEWED` with `reviewed_at = timezone.now()` and `reviewed_by = reviewer_user` while leaving `PrizeAward.status = PENDING`.
- `PrizeClaimService.deliver_claim(claim_id, deliverer_user)`:
  - Locks `PrizeClaim` and `PrizeAward` with `select_for_update()` inside `transaction.atomic()`.
  - Idempotent when already `DELIVERED` (returns existing claim without overwriting `delivered_at`, `delivered_by`, or `award.fulfilled_at`).
  - Requires `claim.status == REVIEWED` (rejecting `SUBMITTED -> DELIVERED` with `400 ValidationError`).
  - Transitions `REVIEWED -> DELIVERED` (`delivered_at = now`, `delivered_by = deliverer_user`) and atomically updates `award.status = FULFILLED` and `award.fulfilled_at = now`.

### APIs & Permissions (`apps/prizes/views.py`, `apps/prizes/urls.py`, `apps/users/manager_permissions.py`)

| Method | Path | Access / Permission | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/prizes/my-awards/<id>/claim/` | `IsAuthenticated` (award owner) | Submits `full_name`, `address`, `identity_number` for a pending `CLAIM_REQUIRED` award; returns `201` with `id`, `award_id`, `status`, `submitted_at` (never echoes PII) |
| `GET` | `/api/prizes/my-awards/` & `/api/prizes/my-awards/<id>/` | `IsAuthenticated` (award owner) | Includes safe read-only claim summary fields (`claim_status`, `claim_submitted_at`, `claim_reviewed_at`, `claim_delivered_at`) without exposing `full_name`, `address`, or `identity_number` |
| `GET` | `/api/prizes/admin/claims/` | `prizes.claims.list` | Manager list of `PrizeClaim` rows ordered by `-submitted_at, -id`; supports `status` and `source_type` (`award__source_type`) filters |
| `GET` | `/api/prizes/admin/claims/<id>/` | `prizes.claims.retrieve` | Manager detail of one `PrizeClaim` row including claimant `full_name`, `address`, `identity_number`, and linked award metadata |
| `POST` | `/api/prizes/admin/claims/<id>/review/` | `prizes.claims.review` | Marks a `SUBMITTED` claim as `REVIEWED` (idempotent on repeat review) |
| `POST` | `/api/prizes/admin/claims/<id>/deliver/` | `prizes.claims.deliver` | Marks a `REVIEWED` claim as `DELIVERED` and fulfills its `PrizeAward` (idempotent on repeat deliver; rejects `SUBMITTED` claims with `400`) |

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/prizes/models.py` | Added `PrizeClaimStatus`, `PrizeClaim`, and `CLAIM_REQUIRED` fulfillment guard in `PrizeAward.clean()` | Claim domain model and lifecycle invariants |
| `apps/prizes/services.py` | Added `PrizeClaimService` (`list_for_admin`, `submit_claim`, `review_claim`, `deliver_claim`) and added `"claim"` to `PrizeAwardService.list_for_user` `select_related` | Transactional claim submission, review, and delivery |
| `apps/prizes/serializers.py` | Added safe claim summary fields to `MyPrizeAwardSerializer`; added `PrizeClaimSubmitSerializer`, `MyPrizeClaimResponseSerializer`, `AdminPrizeClaimSerializer`, `AdminPrizeClaimActionSerializer` | Strict PII isolation on user endpoints and manager review serialization |
| `apps/prizes/views.py`, `apps/prizes/urls.py`, `apps/prizes/admin.py` | Added `MyPrizeAwardClaimViewSet`, `AdminPrizeClaimViewSet`, `AdminPrizeClaimReviewViewSet`, `AdminPrizeClaimDeliverViewSet`, URL routes, and `PrizeClaimAdmin` | User and manager claim APIs and read-only Django admin |
| `apps/users/manager_permissions.py` | Registered `prizes.claims.list`, `prizes.claims.retrieve`, `prizes.claims.review`, and `prizes.claims.deliver` | Manager RBAC |
| `apps/prizes/migrations/0002_prizeclaim.py` | Schema-only `CreateModel` migration for `PrizeClaim` (`0` backfilled rows) | Database schema |
| `locale/ar/LC_MESSAGES/django.po`, `django.mo` | Added 49 Arabic translations and compiled `.mo` | Arabic-first localization |
| `tests/prizes/test_prize_claims.py`, `tests/prizes/test_prize_awards.py` | Added 51 tests in `test_prize_claims.py` (`test_01` through `test_51`) and updated `test_51` field set in `test_prize_awards.py` | Full BE-PRZ-02 coverage |

### Test results (2026-10-07, `ppw-env`)

| Command | Result |
| --- | --- |
| `pytest tests/prizes/test_prize_claims.py -q` | 51 passed |
| `pytest tests/prizes/test_prize_awards.py -q` | 63 passed |
| `pytest tests/prizes tests/referrals tests/lucky_wheel tests/seasons tests/draws tests/promotions tests/test_api_docs.py tests/test_i18n.py -q` | 766 passed |
| `python manage.py makemigrations --check --dry-run` | No changes detected |
| Scratch SQLite DB migration (`migrate` -> `migrate prizes 0001` -> `migrate prizes 0002` -> `migrate prizes zero` -> `migrate prizes`) | Succeeded with `0` `PrizeAward` and `0` `PrizeClaim` rows |

## 27. Reward & Prize Congratulatory Notifications (BE-NOT-01)

Status: **DONE**

### Requirement & Scope Boundary

Verified Arabic product requirements (`docs/rewards/rewarding-system-report.md` Section 10 / PDF pp. 25, 42–43):
1. **Congratulatory notifications when earning points (`PP Coins`) or badges**:
   - Positive `PointTransaction` (`transaction_type = EARN`, `points > 0`) -> congratulatory `POINTS_EARNED` notification showing the earned PP Coin amount.
   - New `BadgeAward` -> congratulatory `BADGE_AWARDED` notification showing the badge name.
2. **Congratulatory notifications when winning a claim-required prize**:
   - New `CLAIM_REQUIRED` `PrizeAward` (`PrizeAwardService.record_claim_required`) -> congratulatory `PRIZE_AWARDED` notification showing the prize title so the winner knows to submit the claim form (`full_name`, `address`, `identity_number`).

**Out of Scope (Simplicity Wins):**
- No notification grouping or daily digests (`BE-NOT-02`).
- No Do-Not-Disturb (`DND`) or per-category user notification preference filtering (`BE-NOT-02`).
- No weekly-draw winner notification while `BE-DRW-02` winner-selection rules remain blocked.
- No `PrizeClaim` submission, manager review, or delivery status notifications.
- No new database models or schema migrations (`0` migrations).
- No historical backfill of notifications for existing `PointTransaction`, `BadgeAward`, or `PrizeAward` rows.

### Integration Points & Anti-Duplication Invariant

| Event Category | Single Integration Boundary | Notification Type | Idempotency Key | Anti-Duplication Rule |
| --- | --- | --- | --- | --- |
| **PP Coin Earning** | `PointService._create_transaction()` (`apps/loyalty/services.py`) | `NotificationType.POINTS_EARNED` (`"points_earned"`) | `f"points_earned:{point_tx.pk}"` | Fires only when a **new** `PointTransaction` row is created with `transaction_type == PointTransactionType.EARN` and `points > 0`. Duplicate idempotent `earn_points` calls, `SPEND`, and `EXPIRE` transactions create `0` notifications. Covers onboarding, bids, listings, ratings, follows, deals, referrals, Lucky Wheel `PP_COINS`, and Season PP payouts without per-caller hooks. |
| **Badge Award** | `BadgeService.award_badge()` (`apps/loyalty/services.py`) | `NotificationType.BADGE_AWARDED` (`"badge_awarded"`) | `f"badge_awarded:{award.pk}"` | Fires only when `created_new=True`. Duplicate idempotent calls (`idempotency_key` or `one_per_profile=True`) create `0` additional notifications. |
| **Claim-Required Prize Win** | `PrizeAwardService._record_award()` (`apps/prizes/services.py`) | `NotificationType.PRIZE_AWARDED` (`"prize_awarded"`) | `f"prize_awarded:{award.pk}"` | Fires only when `created_new=True` **and** `fulfillment_type == PrizeAwardFulfillmentType.CLAIM_REQUIRED`. `PrizeAwardService.record_digital_fulfilled()` creates `0` generic prize notifications because digital PP/Badge rewards already emit their specific `POINTS_EARNED` / `BADGE_AWARDED` notifications at `PointService` / `BadgeService`. |

### Transactional & Delivery Lifecycle (`apps/notifications/services.py`)

- `NotificationService.create_notification(...)` creates the `Notification` DB row inside the caller's active `transaction.atomic()` block:
  - If the reward/prize transaction rolls back, the `Notification` row rolls back with it (`0` orphan notifications).
  - If `NotificationService.create_notification(...)` raises inside the atomic block, the reward/prize transaction rolls back with it.
- Real-time WebSocket (`broadcast_notification`) and Celery/FCM push (`deliver_notification_push.delay(notification.id)`) are scheduled strictly via `transaction.on_commit(...)` and wrapped in best-effort `try/except` blocks so post-commit transport failures never roll back or corrupt committed reward/prize or `Notification` rows.
- Exposed through the existing `/api/notifications/`, `/api/notifications/unread-count/`, `/api/notifications/<id>/mark-read/`, and `/api/notifications/mark-all-read/` endpoints with Arabic-first default copy and English `Accept-Language: en` / `?lang=en` localization.

### Files changed

| File | Change | Reason |
| --- | --- | --- |
| `apps/notifications/models.py` | Attached runtime constants `NotificationType.POINTS_EARNED = "points_earned"` and `NotificationType.PRIZE_AWARDED = "prize_awarded"` and allowed them in `Notification.clean_fields()` without changing field `choices` | Zero-migration notification type constants |
| `apps/notifications/payload.py` | Added parameterized regex matching in `localize_notification_text()` for `POINTS_EARNED`, `BADGE_AWARDED`, and `PRIZE_AWARDED` bodies | Arabic/English request-time localization of dynamic points, badge names, and prize titles |
| `apps/loyalty/services.py` | Added `_resolve_reward_recipient_profile()`, `PointService._create_points_earned_notification()`, and synchronous `BadgeService._schedule_badge_awarded_notification()` inside `transaction.atomic()` | Atomic, idempotent PP earning and badge award congratulatory notifications |
| `apps/prizes/services.py` | Added `_resolve_award_recipient_profile()` and `PrizeAwardService._create_claim_required_notification()` when a new `CLAIM_REQUIRED` `PrizeAward` is created | Atomic, idempotent claim-required prize winner notifications with zero digital duplication |
| `locale/ar/LC_MESSAGES/django.po`, `locale/en/LC_MESSAGES/django.po`, `.mo` | Added Arabic and English translations for `POINTS_EARNED`, `BADGE_AWARDED`, and `PRIZE_AWARDED` titles/bodies | Bilingual localization |
| `tests/notifications/test_reward_prize_notifications.py` | New, 42 tests (`test_01` through `test_42`) | Full BE-NOT-01 coverage |
| `tests/prizes/test_prize_awards.py`, `tests/prizes/test_prize_claims.py`, `tests/lucky_wheel/test_lucky_wheel_spin_execution.py` | Updated pre-`BE-NOT-01` notification-count assertions to reflect `BE-NOT-01` behavior | Align existing tests with live `BE-NOT-01` notifications |

## 28. Task Status

| Task | Description | Status |
| --- | --- | --- |
| BE-REW-01 | Shared UserAccount-level PP Coin wallet ownership | DONE (commit `774d7cc`, tests in `tests/loyalty/test_shared_wallet.py`) |
| BE-REW-02 | Account-level onboarding reward (100 PP once per UserAccount) | DONE |
| BE-REW-03 | Configurable reward rule settings | DONE |
| BE-REW-04 | Canonical completed deal boundary | DONE |
| BE-REW-05 | Decouple RewardEngine from PaymentRequest | DONE |
| BE-REW-06 | Separate badges from point rewards | DONE |
| BE-REW-07 | Active auction bidder reward | DONE |
| BE-REW-08 | Completed deal double points | DONE |
| BE-REW-09 | Successful sale reward (seller, every completed deal) | DONE |
| BE-REW-10 | Mutual deal rating reward | DONE |
| BE-REF-01 | Admin-approved referrals | DONE |
| BE-REF-02 | Referral first-bid inviter reward | DONE |
| BE-REF-03 | Monthly referral challenge | DONE |
| BE-SEA-01 | Seller season scoring (40% sales / 30% ratings / 20% reliability / 10% interaction) | DONE |
| BE-SEA-02 | Finalize season and pay winners once (100,000 / 50,000 / 25,000 PP) | DONE |
| BE-SEA-03 | Admin-configurable season prizes (per-season Rank 1..3 prize config, frozen at start) | DONE |
| BE-PRO-01 | Promotion campaign types + scheduling (`MONTHLY`, `SEASONAL`, `FLASH`) | DONE |
| BE-PRO-02 | Service-level promotion effects (`PromotionCampaignEffect`, non-stacking highest-discount resolution, package + Loft subscription integration) | DONE |
| BE-PRO-03 | Onboarding 20% first auction-highlight discount (account-level entitlement, campaign comparison, trusted atomic consumption) | DONE (actual purchase wiring deferred pending canonical highlight domain) |
| BE-PRO-03A | Harden onboarding highlight benefit no-backfill semantics (`ensure_for_first_profile_onboarding` via `Profile.all_objects`, `discount_percent` scoping) | DONE |
| BE-DRW-01 | Weekly onboarding draw domain + automatic entry (`apps.draws`, `WeeklyDraw`, `WeeklyDrawEntry`, pending assignment, Lucky Wheel onboarding separation) | DONE |
| BE-DRW-02 | Weekly draw finalization + winner-selection requirement gate (`now >= ends_at` close/freeze foundation, frozen `ASSIGNED` participant set, idempotent `POST /api/draws/weekly/<id>/finalize/`) | PARTIAL / BLOCKED (winner-selection rule, winner count, and prize definition require product decision) |
| BE-LW-01 | Lucky Wheel source audit + eligibility / attempt lifecycle hardening (`LuckyWheelService.get_availability`, `LuckyWheelGrantService` / `LuckyWheelSpinService` locking & isolation, client payload guard) | DONE |
| BE-LW-01A | Lucky Wheel lifecycle / API / source-provenance hardening (savepoint-safe `grant_attempt` `IntegrityError` recovery with collision check, `expires_at ASC NULLS LAST` ordering, public API metadata stripping, source-provenance classification) | DONE |
| BE-LW-02 | Lucky Wheel spin outcome + prize execution hardening (`LuckyWheelPrizeSelector` with `secrets.SystemRandom()`, bounded concurrent finite-inventory re-resolution, strict one-of prize validation, atomic rollback, immutable `prize_snapshot`) | DONE |
| BE-PRZ-01 | Shared prize award / fulfillment foundation (`apps.prizes`, `PrizeAward` ledger, `DIGITAL_AUTOMATIC` vs `CLAIM_REQUIRED`, source integrations, user/manager read APIs) | DONE |
| BE-PRZ-02 | Claim-required prize submission, manager review & delivery (`PrizeClaim`, `PrizeClaimService`, `SUBMITTED -> REVIEWED -> DELIVERED`, user claim API, manager claim APIs, PII isolation) | DONE |
| BE-NOT-01 | Reward & prize congratulatory notifications (`PointService.earn_points`, `BadgeService.award_badge`, `PrizeAwardService.record_claim_required`, existing `NotificationService` + post-commit WebSocket/FCM reuse) | DONE |

Other reward work (weekly draw winner selection once product rules are confirmed, notification preferences/DND in BE-NOT-02, and buyer/seller level progression) is tracked in subsequent tickets.




