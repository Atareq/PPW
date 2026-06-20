# Requirements Audit — Section 10: Seller Dashboard (لوحة تحكم البائع)

**PDF Section:** القسم 10 — لوحة تحكم البائع  
**Audited:** 2026-06-20  
**Status:** Implementation complete for all backend-ready items

---

## 10.1 — Requirements (from PDF)

### ملخص سريع (Quick Summary)
| # | Requirement (AR) | Requirement (EN) |
|---|---|---|
| 10.1.1 | عدد المزادات النشطة | Active auctions count |
| 10.1.2 | إجمالي المبيعات | Total sales |
| 10.1.3 | أعلى سعر بيع | Highest sale price |
| 10.1.4 | أهم مزاد (أعلى تفاعل) | Top auction by engagement |

### المحفظة (Wallet)
| # | Requirement (AR) | Requirement (EN) |
|---|---|---|
| 10.1.5 | رصيد PP Coins | PP Coins balance |
| 10.1.6 | رصيد العربون | Earnest / deposit balance |
| 10.1.7 | سجل العمليات | Transaction history |

### الإنجازات (Achievements)
| # | Requirement (AR) | Requirement (EN) |
|---|---|---|
| 10.1.8 | الأوسمة | Badges |
| 10.1.9 | المستوى الحالي | Current level |
| 10.1.10 | الجوائز | Prizes / rewards |

### إدارة المزادات (Auction Management)
| # | Requirement (AR) | Requirement (EN) |
|---|---|---|
| 10.1.11 | إنشاء مزاد جديد | Create new auction |
| 10.1.12 | تعديل / إيقاف | Edit / cancel auction |
| 10.1.13 | نتائج المنتهية | Ended auction results |

### Settings
| # | Requirement (AR) | Requirement (EN) |
|---|---|---|
| 10.1.14 | إعدادات الشحن | Shipping settings |
| 10.1.15 | إعدادات الدفع | Payment settings |
| 10.1.16 | إعدادات الإشعارات | Notification settings → mapped to notifications page |

---

## Backend Gap Analysis

| # | Requirement | Backend | Notes |
|---|---|---|---|
| 10.1.1 | Active auctions count | ✅ `GET /api/insights/seller/home/` → `auction_summary.active_count` | — |
| 10.1.2 | Total sales | ⚠️ `auction_summary.sold_items_count` + `market_summary.sold_items_count` | Count only — no revenue (SAR/EGP) amount anywhere in backend |
| 10.1.3 | Highest sale price | ❌ Not exposed | No per-auction high-price field in any endpoint |
| 10.1.4 | Top auction by engagement | ❌ Not exposed | Backend has aggregate views/bids at seller level; no per-auction ranking |
| 10.1.5 | PP Coins balance | ✅ `package_summary.active_package.remaining_points` | — |
| 10.1.6 | Earnest/deposit balance | ❌ Not implemented | Explicitly absent per AGENTS.md — no wallet model exists |
| 10.1.7 | Transaction history | ❌ Not implemented | No seller-scoped transaction log endpoint |
| 10.1.8 | Badges | ✅ `GET /api/loyalty/badges/my/` | 3 types: `first_order`, `points_starter`, `seller_starter`; auto-award is stubbed only |
| 10.1.9 | Current level | ❌ Not implemented | No profile level model in backend |
| 10.1.10 | Prizes / rewards | ❌ Not implemented | No prizes model in backend |
| 10.1.11 | Create new auction | ✅ `POST /api/auctions/` | — |
| 10.1.12 | Edit auction | ✅ `PATCH /api/auctions/<id>/` | title, description, tags, chat_enabled only; before bids only |
| 10.1.12 | Cancel auction | ✅ `POST /api/auctions/<id>/cancel/` | Cancel only — no pause/suspend option |
| 10.1.13 | Ended auction results | ✅ `GET /api/auctions/my_auctions/?status=closed` + `GET /api/auctions/<id>/` | Full winner + bid data available |
| 10.1.14 | Shipping settings | ❌ Not implemented | Explicitly out of scope per AGENTS.md — no shipping workflow |
| 10.1.15 | Payment settings | ✅ `GET /api/payments/requests/` | PaymentsPage exists — covers viewing payment requests |
| 10.1.16 | Notification settings | ✅ `GET /api/core/notifications/` | Mapped to NotificationsPage per user decision |

---

## Flutter Implementation Status (post-implementation)

### Implemented 2026-06-20

| # | Requirement | Flutter | Files |
|---|---|---|---|
| 10.1.1 | Active auctions count | ✅ | `home_seller_metrics_section.dart` — `activeLiveAuctions` |
| 10.1.2 | Total sold count | ✅ | `seller_stats_page.dart` — `totalSoldCount` (auction + market sold items) |
| 10.1.5 | PP Coins balance | ✅ | `home_page.dart` — `HomeAccountRow` pointsLabel |
| 10.1.8 | Badges | ✅ | `loyalty/view/pages/seller_badges_page.dart` — grid view; wired from drawer |
| 10.1.11 | Create auction | ✅ | FAB on home → `AuctionCreatePage` |
| 10.1.12 | Edit auction | ✅ | `seller_my_auctions_page.dart` — popup menu → `AuctionEditPage` |
| 10.1.12 | Cancel auction | ✅ | `seller_my_auctions_page.dart` — popup menu → confirm dialog → `AuctionCancelRequested` |
| 10.1.13 | Ended auction results | ✅ | `seller_my_auctions_page.dart` — "منتهية" tab filtered by `isEnded`; taps into `AuctionDetailPage` |
| 10.1.15 | Payment settings | ✅ | `home_drawer.dart` — `_SellerPaymentsTile` → `PaymentsPage` |
| 10.1.16 | Notification settings | ✅ | `home_drawer.dart` — `_NotificationsTile` → `NotificationsPage` |

### Enrichments (beyond minimum)
- `SellerHomeSummary` model expanded from 13 flat fields to 30+ fields covering all 5 backend sections (auction, market, engagement, trust, package)
- `real_seller_home_remote_datasource.dart` — `fetchHomeSummary()` now calls `GET /api/insights/seller/home/` and parses full nested response (was `return null`)
- `HomeSellerMetricsSection` — added `_EngagementRow` showing followers count, total bids received, average rating (renders only when data is non-zero)
- `seller_stats_page.dart` — "مبيعات اليوم" renamed to "إجمالي المبيعات"; now uses `totalSoldCount` computed getter
- `real_auctions_remote_datasource.dart` — fixed null-aware map entry syntax in `updateAuction`

---

## Remaining Backend Gaps (blocked — cannot implement Flutter side)

| # | Requirement | Reason blocked |
|---|---|---|
| 10.1.3 | Highest sale price | No endpoint or field exposes per-auction high price |
| 10.1.4 | Top auction by engagement | No per-auction ranking endpoint — only seller-level aggregates |
| 10.1.6 | Earnest/deposit balance | No wallet model exists in backend |
| 10.1.7 | Transaction history | No seller-scoped transaction log endpoint |
| 10.1.9 | Current level | No profile level model |
| 10.1.10 | Prizes / rewards | No prizes model |
| 10.1.14 | Shipping settings | Explicitly out of scope in AGENTS.md |

---

*Last updated: 2026-06-20*
