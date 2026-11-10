# November 10 historical-lineage regression evidence

`lineage_sources.json` preserves four source records from the private retry04 collection (three curated stories plus the related OCHA discovery wrapper). Source URLs, publisher dates and snippets remain unchanged.

`lineage_prior.json` is produced by the existing `memory_row` function from the actual archived public curation manifests for 2026-09-02 (EU action), 2026-07-23 (35 detainees), and 2026-09-13 (generic OCHA report). No new event dates are inferred.

On the pre-fix 49a91e1 code, the normalized attack, rain report and Board of Peace response each incorrectly classify as a major update to these unrelated records. The conservative fix requires existing exact normalized URL or title identity for Gaza historical links. Topic similarity and matching generic event fields are insufficient: the current curator assigns event_date from published_at, and historical memory lacks provenance-bound event identity.

Tradeoff: genuinely continuing events with changed URLs and headlines remain new stories until stronger explicit identity evidence is available. They are not excluded, filtered or removed. Exact URL repeats and updates and same-title continuations retain their existing behavior. This does not change source selection, publication gates, or other desks' lineage logic.

Verification: six in-memory unittest cases pass, including actual normalized Nov10 visit/attack cases, these three historical mismatches, exact repeats/updates, and opposite geography/time hospital-fuel reports. Matching unverified event fields alone are also rejected. Full pytest was not run: filesystem free space was below the unchanged 2GiB reserve. No reserve bypass or deletion.
