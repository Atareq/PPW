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
- Lucky Wheel is unchanged: `LuckyWheelGrantService.grant_onboarding_attempt()`
  is still called separately and still grants one attempt per created profile
  (`lucky_wheel:onboarding:{profile_id}`).

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
| `REFERRAL_INVITER_POINTS = 30` | `apps/referrals/services.py` | Product config -> `REFERRAL_APPROVED` |
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

### Rule catalog and defaults

| Code | Default | Required fields | Read by code | Status |
| --- | --- | --- | --- | --- |
| `ACCOUNT_ONBOARDING` | 100 PP | `points` | Yes (`RewardEngine.grant_account_onboarding_reward`) | Confirmed |
| `AUCTION_PUBLISHED` | 50 PP | `points` | No (engine still pays a hard-coded 50) | Confirmed value |
| `ACTIVE_AUCTION_BIDDER` | 50 PP, threshold 5 | `points`, `threshold` | No | Confirmed value; reward not implemented |
| `MUTUAL_RATING` | 20 PP | `points` | No | Confirmed value; reward not implemented |
| `REFERRAL_APPROVED` | 30 PP | `points` | No (referrals still use `REFERRAL_INVITER_POINTS = 30`) | Confirmed value |
| `REFERRAL_FIRST_BID` | 200 PP | `points` | No | Confirmed value; reward not implemented |
| `DEAL_COMPLETED` | none | none | No | **Pending business clarification** |
| `POSITIVE_RATING` | none | none | No | **Pending business clarification** |

Pending rules have `points=None`; no value was invented:

- `DEAL_COMPLETED`: the closest current behavior is market-only
  (`int(total_price)` PP + 100 PP bonus to buyer and to seller on an approved
  `MarketPaymentRequest`). No document defines one deal-completion rule across
  auction and market deals.
- `POSITIVE_RATING`: current code pays 30 PP for any rating received, whatever
  the stars. No document defines what counts as positive or its value.

All defaults have `enabled=True`, `multiplier=None`, and `configuration={}`.
`multiplier` exists for amount-based rules such as the current market
`int(total_price)` payout, but no rule uses it yet.

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
check constraint). No data migration. Applied by the test database; not applied
to any local or production database as part of this task.

### Remaining risks

- Only `ACCOUNT_ONBOARDING` is live. Managers can edit `AUCTION_PUBLISHED` and
  `REFERRAL_APPROVED` today, but those payouts still use hard-coded 50 and 30,
  so edits have no effect until the rules are wired. The API marks them
  `is_implemented=false`.
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
  sale.
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

## 8. Task Status

| Task | Description | Status |
| --- | --- | --- |
| BE-REW-01 | Shared UserAccount-level PP Coin wallet ownership | DONE (commit `774d7cc`, tests in `tests/loyalty/test_shared_wallet.py`) |
| BE-REW-02 | Account-level onboarding reward (100 PP once per UserAccount) | DONE |
| BE-REW-03 | Configurable reward rule settings | DONE |
| BE-REW-04 | Canonical completed deal boundary | DONE |
| BE-REW-05 | Decouple RewardEngine from PaymentRequest | DONE |
| BE-REW-06 | Separate badges from point rewards | DONE |

Other reward work (referrals redesign, seasons, 5-bidder rewards, deal
completion rewards, mutual ratings, weekly draws) is not tracked in this file
yet.
