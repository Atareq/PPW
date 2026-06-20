# Commit: 1ce4307 — added price serializer
**Date:** 2026-06-18  
**Author:** Ahmed Ali

---

## Summary of Changes

| Area | Change Type | Description |
|------|-------------|-------------|
| Auctions | Updated | `current_price` in `AuctionListSerializer` converted to a `SerializerMethodField` |

---

## Auctions — `current_price` as SerializerMethodField

**File:** `apps/auctions/serializers.py`

### What changed
`current_price` was already present in the `AuctionListSerializer` fields list (added in `a9ed138`), but was being read as a plain model field. It is now a `SerializerMethodField` that pulls the value from the first auction item's `current_price` property.

Returns `null` if the auction has no items.

### Old Behavior
`current_price` was a direct model field read — returned the auction-level field value (if any).

### New Behavior
```python
def get_current_price(self, obj) -> Decimal | None:
    items = obj.items.all()
    if items:
        return items[0].current_price
    return None
```

### Response (unchanged shape, corrected value source)
```json
{
  "id": 3,
  "title": "Spring Auction",
  "current_price": "1200.00",
  ...
}
```

Returns `null` when the auction has no items.

---
