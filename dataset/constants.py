"""Cross-cutting constants for the dataset plugin.

The vendor money path is DECOUPLED from the central marketplace plugin: dataset
stamps the buyer invoice line's ``extra_data`` with this LOCAL key under the
selling vendor's user id, and ``marketplace`` credits the vendor from it on
``invoice.paid`` — no plugin ever imports the other. ``VENDOR_ID_KEY`` is a
local copy of the documented ``MARKETPLACE_VENDOR_ID_KEY`` convention, pinned to
the literal by a contract test (DRY without inverting the dependency arrow).
"""

VENDOR_ID_KEY = "vendor_id"
