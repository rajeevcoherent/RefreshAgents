# House playbook learned from past ME workbooks

72 markets (April, July, June, May); train 53 / held-out test 19 (split by market folder). Only scale-free quantities are learned - shares, growth spreads, history vs forecast - never a market's absolute size.

## Markets by category

- Chemicals & materials: 13 - Global Adhesives & Sealants Market, Global Technical Foam Market, Asia and Middle East Water Treatment Chemical Market, U.S., China & Europe Glass Packaging Market, North America & Europe Azelaic Acid Market, North America and Europe Pelargonic Acid Market, Global Adhesives & Bonding Market, Global Lamination Adhesives for Flexible Packaging Market, U.S., Mexico, & Brazil Resin Compound Market, Global Advanced Surface Engineering & Protective Coatings Market, India Industrial Gas Market, India Industrial Gas Market Cross Section, U.S. Used Cooking Oil Market Report
- Pharmaceuticals & APIs: 12 - Global Amphetamine + Deaxmphetamine Market, Global Buprenorphine Market, Global Chronic Spontaneous Urticaria Market_Coherent Market Insights, U.S Povidone Iodine Nasal Spray Market, U.S. Providone Iodine Market, Global Next Generation Probiotics Market, Global Water for Injection Container Market, Global Codeine Market, Global Apomorphine API & FDF Market, Global Apomorphine API Market, Global Sodium Oxybate API Market, Global Sodium Oxybate API + FDF Market
- ICT, software & digital services: 10 - Global Cyber-Physical Systems Security Market, Global Retail Self Scanning Solutions Market, North America and Europe Sales Training Market, Global Digital Twin Market, Global Head End System Market, Global PLC Market, Global Supply Chain Risk Management Market, U.S. Digital Signage Market, Global Well Planning & Engineering Software Market, Global Creator Economy Market
- Medical devices & healthcare services: 10 - Global Gel Ice Pack Market, Latin America Bone Cement Market, Collagen Membrane Market, Global High Intensity Focused Ultrasound (HIFU) Market, India Smart Hospitals & Digital Transformation Market, Global Athletic Tape Market, Global Laser Based Photo Rejuvenation Devices Market, North America & US Trocar Market, Global Protein Biomarkers Market, U.S. Clostridium Difficile Testing Market
- Industrial automation, electronics & components: 10 - Global High Energy Laser Market, Global Process Analyzer Market, Global Spectral Sensor Market, North America Electronic Design Services Market, Global Life Sciences And Pharmaceutical Industrial Automation And Control Systems Market, Global Waveguide Market, Global Industrial Automation Systems Market, India Process & Factory Automation Market, Global Industrial and Autonomous Robotics Market, Global MINIATURE Ball Bearing Market
- Food & beverage: 7 - Global Pizza Market, Middle East Agriculture Market, Global Decaf Coffee Market, Global Avocado Oil Market, Global Bourbon Whisky and Scottish Whiskey Market, Africa Agriculture Market, Europe Functional Dairy Ingredients Market
- Energy, water & utilities: 4 - Global Used Cooking Oil Collection Market, India Seawater Reverse Osmosis (SWRO) & High-Rejection Industrial RO Membrane Market, North America Greywater Recycling System Market, Global Waste Oil Market
- Construction, travel & other services: 3 - GCC Outbound Tourism Market, U.S. Attic Ladder Market, Global Superyacht Market
- Consumer goods & retail: 2 - Saudi Arabia Aluminium & Wooden Kitchens Market, India Mobile Phone Accessories Market
- Industrial machinery & equipment: 1 - Global Liquid Hydrogen Tank Market

## Corpus bands (p10 / median / p90, n markets)

| Feature | p10 | median | p90 | n |
|---|---|---|---|---|
| growth.fcst_cagr | 5.2% | 7.8% | 13.6% | 72 |
| growth.fcst_minus_hist | +0.44% | +0.65% | +1.02% | 72 |
| growth.price_cagr | 0.8% | 1.2% | 1.9% | 30 |
| region.share.NA | 27.7% | 35.8% | 40.1% | 43 |
| region.share.Europe | 18.2% | 25.9% | 29.0% | 43 |
| region.share.APAC | 17.5% | 24.5% | 38.5% | 43 |
| region.share.LATAM | 3.4% | 6.0% | 8.9% | 43 |
| region.share.MEA | 3.5% | 5.8% | 10.2% | 43 |
| region.cagr_gap.NA | -2.01% | -0.38% | +0.30% | 43 |
| region.cagr_gap.Europe | -1.58% | -0.82% | +0.80% | 43 |
| region.cagr_gap.APAC | +0.75% | +1.60% | +2.87% | 43 |
| region.cagr_gap.LATAM | -2.45% | -1.22% | +0.41% | 43 |
| region.cagr_gap.MEA | -3.89% | -0.83% | +1.11% | 43 |
| region.cagr_range | 2.3% | 3.7% | 7.6% | 43 |
| country.share.U.S. | 82.2% | 87.6% | 92.1% | 46 |
| country.share.Canada | 7.6% | 12.4% | 16.2% | 46 |
| country.share.Germany | 15.4% | 23.5% | 30.4% | 47 |
| country.share.U.K. | 12.0% | 17.2% | 21.7% | 45 |
| country.share.France | 11.2% | 15.1% | 18.8% | 47 |
| country.share.Italy | 7.5% | 11.6% | 15.8% | 45 |
| country.share.Spain | 6.2% | 9.4% | 12.8% | 45 |
| country.share.Russia | 4.5% | 6.2% | 8.9% | 41 |
| country.share.China | 24.9% | 32.2% | 45.4% | 42 |
| country.share.Japan | 10.0% | 15.8% | 24.4% | 42 |
| country.share.India | 9.2% | 18.6% | 23.1% | 42 |
| country.share.South Korea | 5.5% | 8.3% | 11.5% | 42 |
| country.share.Brazil | 33.4% | 39.6% | 46.4% | 44 |
| country.share.Mexico | 23.5% | 28.6% | 36.1% | 44 |
| country.share.Argentina | 8.9% | 13.2% | 19.7% | 43 |
| country.share.South Africa | 14.2% | 39.7% | 49.8% | 42 |
| country.rest_share | 7.2% | 15.3% | 19.7% | 52 |
| country.cagr_gap_sd | +0.96% | +1.30% | +2.23% | 53 |
| seg.top_share | 39.1% | 51.3% | 67.9% | 72 |
| seg.cagr_spread | 1.8% | 2.9% | 4.7% | 72 |
| seg.cagr_dev | 0.5% | 0.9% | 1.5% | 72 |
| seg.sub_cagr_dev | 0.5% | 0.9% | 2.4% | 36 |
| seg.country_mix_sd | 0.7% | 0.9% | 2.2% | 59 |

Directional frequencies:
- share of markets with forecast CAGR > historical CAGR: 97%
- share of global markets where APAC grows fastest: 86%
- share of global markets where Europe grows slowest: 28%
- share of global markets where NA is the largest region: 72%
- share of global markets where NA grows slower than global: 65%
- share of markets where smaller segments grow faster (seg.small_fast < 0): 39%

## Rules - market economics

| id | status | support (train / test) | rule | why |
|---|---|---|---|---|
| R10 | kept | 0.806 (n=36) / 0.7 (n=10) | U.S. share of North America is typically 84–93%. | The U.S. dominates NA in nearly every market, but Canada holds a small share. |
| R11 | kept | 0.848 (n=33) / 0.778 (n=9) | China share of APAC is typically 18–42%. | China is a major APAC market, but Japan, India, South Korea also contribute. |
| R12 | kept | 0.818 (n=33) / 1 (n=9) | India share of APAC is typically 9–24%. | India's share is moderate, rarely above one-quarter of APAC. |
| R13 | kept | 0.853 (n=34) / 1 (n=10) | Brazil share of Latin America is typically 33–47%. | Brazil is the largest LATAM market, but Mexico and Argentina are significant. |
| R14 | kept | 0.722 (n=36) / 0.7 (n=10) | Canada share of North America is typically 7–15%. | Canada is a stable secondary market within NA. |
| R16 | kept | 0.833 (n=36) / 0.889 (n=9) | U.K. share of Europe is typically 12–22%. | U.K. is consistently a major European market. |
| R17 | kept | 0.811 (n=37) / 0.8 (n=10) | France share of Europe is typically 11–19%. | France is a stable top-three European market. |
| R18 | kept | 0.879 (n=33) / 0.889 (n=9) | Japan share of APAC is typically 10–25%. | Japan is a mature APAC market, second or third in size. |
| R19 | kept | 0.758 (n=33) / 0.778 (n=9) | South Korea share of APAC is typically 5–10%. | South Korea is a small but consistent APAC player. |
| R2 | kept | 0.811 (n=53) / 0.842 (n=19) | Root forecast CAGR usually falls between 4.7% and 13.7%. | Across markets, long-term growth rarely exceeds these bounds. |
| R21 | kept | 0.774 (n=53) / 0.895 (n=19) | Segment CAGR spread (fastest minus slowest top-level segment) is typically 1.8–4.7 pp. | Segment growth differences are moderate and rarely extremely wide. |
| R22 | kept | 0.792 (n=53) / 0.895 (n=19) | Segment share-weighted mean absolute CAGR deviation from root is typically 0.4–1.5 pp. | Segment CAGRs cluster around the root; extreme deviations are rare. |
| R23 | kept | 0.792 (n=53) / 0.842 (n=19) | Segment HHI (concentration) is typically 0.27–0.58. | Markets are moderately concentrated, neither fragmented nor monopoly. |
| R25 | kept | 0.81 (n=42) / 0.818 (n=11) | Spread of country CAGRs around their region's CAGR (std) is typically 0.9–2.2 pp. | Country growth within a region is relatively homogeneous. |
| R3 | kept | 0.941 (n=34) / 1 (n=9) | APAC region CAGR gap should be positive, i.e. APAC grows faster than the global root. | APAC is the fastest-growing region in the vast majority of markets. |
| R5 | kept | 0.765 (n=34) / 1 (n=9) | North America share of the root market is typically 28–41%. | NA is the largest region in most markets, but not extremely concentrated. |
| R6 | kept | 0.765 (n=34) / 0.778 (n=9) | Europe share of the root market is typically 20–32%. | Europe's share is moderate and stable across markets. |
| R7 | kept | 0.765 (n=34) / 0.778 (n=9) | APAC share of the root market is typically 14–35%. | APAC share varies with the market's development but rarely leaves this range. |
| R8 | kept | 0.853 (n=34) / 1 (n=9) | Latin America share of the root market is typically 3–9.5%. | LATAM is usually a small but non-negligible region. |
| R9 | kept | 0.824 (n=34) / 0.778 (n=9) | Middle East & Africa share of the root market is typically 3–9%. | MEA is consistently a small single-digit share in global markets. |
| R15 | weak | 0.811 (n=37) / 0.6 (n=10) | Germany share of Europe is typically 15–28%. | Germany is the largest European market, but not overwhelming. |
| R20 | weak | 0.736 (n=53) / 0.684 (n=19) | Top-level segment share (largest segment) is typically 41–69%. | Most markets have a leading segment but not an extreme monopoly. |
| R4 | weak | 0.794 (n=34) / 0.667 (n=9) | Region CAGR spread (fastest minus slowest region) is usually 2.5–7.7 pp. | Regional growth dispersion tends to be moderate and bounded. |

## Rules - house conventions

| id | status | support (train / test) | rule | why |
|---|---|---|---|---|
| R1 | kept | 0.849 (n=53) / 0.895 (n=19) | Forecast minus historical CAGR is typically 0.4–1.1 percentage points. | Analysts set forecasts modestly above history by a consistent margin. |
| R24 | kept | 0.889 (n=45) / 0.857 (n=14) | Segment mix variation across leaf countries (std) is typically below 1.9 pp. | The team builds sheets with near-identical segment mixes across countries. |

## How analysts sized the base year (from their Secondary / Base Number sheets)

### Global Amphetamine + Deaxmphetamine Market - mixed (Medium quality notes)
The notes do not show a single final base-year dollar value; they support a mixed, U.S.-centric sizing that triangulates supply-side quotas/trade data with demand-side dispensing, pricing, and patient counts. The supply chain starts with DEA aggregate production quota (42.4k kg) and allotted quota (38.418k kg); applying the note that retail distribution is about 70% of allotted quota gives roughly 26.9k kg retail, with imports/exports checked separately (U.S. amphetamine imports of 11,042 kg in 2020, 13,920 kg in 2021, 8,685 kg in 2022, ~13,043 kg in 2023). Demand is cross-checked via amphetamine pills dispensed (100M–200M pills/month), prescriptions (2.0M–4.5M/month), lisdexamfetamine pills (10M–60M/month), real per-pill prices, and a patient base of 15.5M diagnosed U.S. adults with about one-third taking stimulant medication.
- lesson: Use official production quotas and trade flows as a physical-volume anchor, then triangulate with dispensing/prescription counts and prices rather than relying on one source.
- lesson: Keep quota, allotted quota, retail distribution, exports, and imports separate to avoid double counting when converting physical volume to market value.
- lesson: Label graph-only or percentage-only inputs as inferred, and document the base year and price year because real per-pill prices change the value estimate materially.

### Global Chronic Spontaneous Urticaria Market_Coherent Market Insights - company revenue roll-up (Medium quality notes)
The notes size the CSU market mainly by rolling up company-reported product revenue and allocating it to CSU. Xolair all-indication revenue is split into Genentech U.S. (US$2,735M) and Novartis ROW (US$1,643M), totaling US$4,378M; the CSU portion is put at US$1,554M for 2024 and adjusted from 26.8% launch-year adoption to 35.5%, producing US$2,116M. Dupixent revenue is allocated using an 8% CSU share and CSU-specific revenue/share figures, while Rhapsido, OMLYCLO, Barzolvolimab and JYB1904 are noted as launch/pipeline estimates. The resulting 2025 base value is labeled in US$ million, with Xolair's 2025 provided value noted as 1,466 and 2,500 in the notes.
- lesson: Use company-reported product revenue by indication as a triangulation anchor, but separate total-product revenue from indication-specific allocation and document the allocation share.
- lesson: When adjusting adoption, keep launch-year and target-year assumptions explicit and stress-test them because they materially move the base-year value.
- lesson: Track pipeline assets separately from marketed-drug revenue roll-up so base-year sizing is not confused with forecast-year additions.

### Latin America Bone Cement Market - unclear (Low quality notes)
The notes contain market segmentation headings (product type, viscosity, application, distribution channel) and several source links, but no base-year market value, volume, price, patient count, procedure count, revenue roll-up, trade value, or CAGR values. They also include 2020 regulatory and distribution news about specific bone cement products. There is no chain of numbers from source to a Latin America bone cement base-year value, so the sizing method cannot be reconstructed from these notes.
- lesson: A market-sizing workbook must preserve the actual base-year value and the arithmetic chain; segmentation tables and report TOCs alone are not sizing evidence.
- lesson: Regulatory approvals and distribution partnerships can support product-level assumptions, but they do not substitute for market size, volume, price, or procedure-based anchors.
- lesson: When relying on third-party reports, note the exact estimate, year, geography, and scope so another analyst can distinguish cited estimates from original derivation.

### Global Technical Foam Market - trade data (Low quality notes)
The notes point to a trade-data-led sizing approach: UN Comtrade and India EXIM records for foam products under HS 94 are the only quantitative market sources listed. Europur and ACC CPI/UNEP TEAP links appear to define product scope and regulatory context (PU foam, HFC phase-down), while the World Bank release provides macro outlook context. The notes do not show actual trade values or calculation steps, so the base-year value cannot be reconstructed from them.
- lesson: Use official trade databases and explicit HS/product codes for a bottom-up base-year anchor, but verify the code isolates the target market.
- lesson: Triangulate trade data with industry-association definitions and regulatory scope notes; keep macro sources for forecasting context, not for the base size.
- lesson: Document extracted values and calculation steps, because a source list without numbers cannot support a defensible market size.

### U.S Povidone Iodine Nasal Spray Market - unclear (Low quality notes)
The notes do not contain a sizing chain for the U.S. povidone iodine nasal spray market. They describe a compound-management market using an R&D-linked economic proxy: global biopharma R&D spend, discovery/preclinical shares (~25–33%), discovery phase (20–25%), compound handling (1–2%), small-molecule share (82–88%), and HTS/storage/logistics components. No number or source in the notes links those inputs to povidone iodine nasal spray units, patients, treatment cost, trade data, or U.S. revenue.
- lesson: Check that the research notes actually refer to the target market before accepting a sizing method; adjacent-market proxies can be misleading.
- lesson: When using an R&D-spend proxy, document the spend base, phase share, handling share, and units so the calculation is auditable.
- lesson: Keep evidence for the target market separate from evidence for a different but related market, and flag mismatches as low quality.

### U.S. Providone Iodine Market - mixed (Low quality notes)
The notes do not size U.S. Povidone Iodine; they explicitly describe a compound-management market sized by R&D-linked economic proxy modeling. The chain starts from third-party global biopharma R&D spending (IFPMA, BioPharmaTrend, R&D World, EFPIA/Japan R&D data), then applies early/discovery-phase budget shares (~20–33%), compound-handling share (1–2%), and small-molecule share (82–88%) as proxies. Additional line items for HTS screening, sample logistics, and library storage are noted, but the notes do not fully reconcile them into a clean base-year calculation.
- lesson: When direct market data are unavailable, an R&D-spend × phase-share × handling-share proxy can be transparent, but every percentage must be sourced and reconciled.
- lesson: Keep research notes aligned to the target market; these notes size compound management, not U.S. Povidone Iodine, so the base-year value should not be reused.
- lesson: Label units and distinguish years, dollar amounts, percentages, and derived line items to avoid ambiguous calculations.

### Asia and Middle East Water Treatment Chemical Market - mixed (Low quality notes)
The notes do not show a completed base-year value. They collect third-party/global and India market references plus two company investor documents, then provide a detailed USD/ton ASP benchmark table across water-treatment chemical segments. The implied sizing chain is a mixed approach: use cited market reports/company context for market scope and combine with ASP benchmarks (and volumes from sources not documented in the notes) to build segment values; however, the volume-to-value bridge is not shown.
- lesson: A market-sizing workbook should keep a visible volume/units column and base-year value; a price table alone cannot document how the estimate was built.
- lesson: Tag every extracted number with source, year, geography, and segment definition; unlabeled rows and missing dates make the method hard to audit.
- lesson: Use third-party market estimates for triangulation, but reconcile them to ASP benchmarks and company filings rather than mixing them without an explicit bridge.

### U.S., China & Europe Glass Packaging Market - unclear (Low quality notes)
The notes list only a FEVE Europe glass-industry page with 160 production sites and €610 million/year investment, an AIGMF container-glass segment PDF, and a FRED series identifier (WPU1381). No production tonnage, shipment units, trade values, prices, revenue, or base-year market value are recorded, so the chain from these sources to a U.S./China/Europe glass-packaging base-year size cannot be reconstructed. The notes therefore do not support classifying the sizing as supply-side, trade, revenue roll-up, units x ASP, or any other specific method.
- lesson: Record the extracted number, unit, year, and source location in the note itself; a URL alone is not a reusable sizing input.
- lesson: Site counts and investment amounts are not market-size volumes unless converted with capacity/output/utilization or price assumptions.
- lesson: A price index such as a FRED PPI series needs a quantity or revenue base before it can produce a market value.

### Global Used Cooking Oil Collection Market - cited third-party estimate (Medium quality notes)
The notes do not show a bottom-up calculation. They record a single 2022 global UCO volume of 3.7 billion gallons from a GlobalData UCO Supply Outlook PDF, plus qualitative flags for highest-supply regions and largest exporter. The base-year market size therefore appears taken directly as a third-party supply estimate, with CE Delft/CGIAR references used as background rather than numerical derivation.
- lesson: When collection data are fragmented, start from a named third-party supply outlook and clearly label the base year and units.
- lesson: Use regional feedstock/NGO reports to sense-check qualitative claims such as highest-supply regions and largest exporter, even if they do not supply the headline number.
- lesson: Do not treat a single cited volume as a full market build unless scope is reconciled, e.g. collected vs available UCO and global vs exportable supply.

### Saudi Arabia Aluminium & Wooden Kitchens Market - mixed (Medium quality notes)
The notes triangulate three approaches for 2026. Household expenditure uses 9.92 million households, a SAR 78.1 billion furnishings/household-equipment pool, and a 0.05 kitchen-cabinetry allocation to reach SAR 3.91 billion. Production/trade adds SAR 2.80 billion domestic manufacturing plus SAR 501.3 million wooden imports and SAR 124.4 million aluminium import proxy, subtracts SAR 3.9 million exports to get SAR 3.42 billion apparent consumption, then applies a 15% design/distribution/installation uplift to reach SAR 3.93 billion. Installation/pricing adds 396,700 replacement, 50,000 new residential, and 35,700 commercial/B2B kitchens to get 482,400 installations and a SAR 3.96 billion value; the final triangulated average is SAR 3.93 billion, or USD 1.05 billion.
- lesson: Triangulate independent demand-side and supply-side methods; convergence across approaches is stronger than a single chain.
- lesson: Separate factory/import-level value from end-market value, and state the distribution/installation uplift assumption explicitly.
- lesson: Cross-check installation counts via replacement, new-build, and commercial demand, and make replacement interval and penetration assumptions clear.

### India Smart Hospitals & Digital Transformation Market - installed base x spend (Medium quality notes)
The analyst sized 2025 India smart hospitals/digital transformation bottom-up as ∑(hospitals by cohort × annual purchase incidence × average annual spending). Total hospitals were anchored at 69,000–70,000 (43,400 private/25,700 public from Healthark), then split into large/medium/small/micro cohorts with assumed digital-infrastructure participation and purchase incidences. Annual spend baskets started from US$25,000 for very small/micro hospitals and applied spending indices (2.4× small, 8.8× medium, 36× large), producing the noted 2025 value of US$75.3B / INR 718,362 crore and trajectory to US$80.6B in 2026 and US$255.8B in 2033.
- lesson: Anchor total target units from a credible third-party count, then segment by ownership/size before applying spend.
- lesson: Use a transparent formula and explicit purchase-incidence assumptions so top-down market values can be reconciled to bottom-up sums.
- lesson: Stress-test spend baskets with a base unit and indices; document every assumed multiplier.

### Global Athletic Tape Market - unclear (Low quality notes)
The notes show a volume table in Mn Units split by product type (Athletic Cloth Tape and Athletic Kinesiology Tape) across six columns, with only North America, Latin America, and Asia Pacific labeled. Summing the listed rows gives 31.836 Mn Units. A standalone figure of 304.9 appears, but no ASP, revenue roll-up, or explicit third-party attribution is recorded, so the base-year value-sizing chain cannot be fully reconstructed from the notes alone.
- lesson: Record whether a headline figure is value (USD Mn) or volume (Mn Units); ambiguous units make the base-year sizing unreconstructable.
- lesson: If converting volume to market value, explicitly note the ASP and its source in the workbook.
- lesson: Label every regional/product column in the notes so row sums can be traced and reconciled to any cited total.

### U.S. Attic Ladder Market - cited third-party estimate (Low quality notes)
The notes show a top-line 'Market Value US$ Bn' entry of 2,143 and a 'U.S. Value Mn' entry of 846.3 for attic ladders. They also list product-type values for folding, telescoping, scissor, and other/sliding attic ladders across five columns, but the notes do not state units, years, source, or how the U.S. value was derived from the product splits. The base-year U.S. value therefore appears to be a cited market estimate with segment values attached, not a bottom-up calculation.
- lesson: Record the source URL, publication date, geography, and unit scale for any top-line market value before carrying it into a model.
- lesson: Check that product-type segment values reconcile to the stated total and clearly label units and years; these notes leave both ambiguous.
- lesson: When relying on a third-party estimate, triangulate the top-line value with an independent unit/price or company-revenue check if available.

### Global Bourbon Whisky and Scottish Whiskey Market - units x ASP (Low quality notes)
The notes appear to size the base year by multiplying bottle volume by average per-bottle price to derive a global whisky/Scotch whisky market value in USD BN, with separate rows for global whisky and Scotch whisky. The workbook also carries third-party global market values and applies cost-category shares (raw materials, aging/storage, bottling/packaging, logistics, marketing) to allocate that value. Cask/barrel capacities, bottle-per-barrel ratios, and oak-type shares are supporting supply-side/conversion references rather than the primary demand sizing chain.
- lesson: When using volume × price, keep bottle-volume units (millions vs billions) and currency explicit; a unit slip changes market size by 1000×.
- lesson: Use third-party market totals and cost-share stacks as triangulation, but reconcile them to the volume × ASP calculation before finalizing.
- lesson: For cask/barrel-derived conversions, separate physical yield assumptions (litres, bottles per barrel) from market-value sizing; don’t let supply-side barrel data substitute for demand-side revenue.

### Global Laser Based Photo Rejuvenation Devices Market - mixed (Low quality notes)
The notes record previous-report market values in a 'Value US$ Million' table, including a Laser Based Photo Rejuvenation Devices row. A 'Calculation Part' shows 2,734 and 0.27, which arithmetically yields about 738.18; this is consistent with a top-down parent-market value times an assumed share. The row also carries 1.013e+04 and 1, but the notes do not explain how those reconcile to the calculation. Sources cited are a GizmoSpring beauty-device report PDF, a Mirae Asset securities PDF, and a Claude chat link.
- lesson: Show the full top-down arithmetic explicitly—parent market, share, units, and resulting segment value—so the base-year estimate is auditable.
- lesson: Separate third-party anchor values from analyst assumptions such as share percentages, and label currency, year, and scope for each.
- lesson: Trace any AI-chat or summary note back to the original PDF page/table; do not rely on a chat link as the source of record.

### North America & US Trocar Market - company revenue roll-up (Medium quality notes)
The notes point to a base year built from company-level trocar revenue estimates and implied market shares, not from procedure or unit volumes. B. Braun/Aesculap NA inputs of 917.2 with a 5.5% share imply roughly US$16.7m NA market if 917.2 is US$000, and Mölnlycke’s NA/US trocar revenue is modeled at ~US$0.28–0.51m. The workbook then settles on North America trocar revenue of US$16–24m and U.S. revenue of US$11–18m. Detailed ASP tables by product type, usability, and size look like price cross-checks because no unit/procedure volumes are shown.
- lesson: If unit volumes are unavailable, back into market size from company revenue estimates divided by defensible share assumptions, then triangulate across multiple companies.
- lesson: Use public ASP/price-list ranges to sanity-check revenue-derived market sizes, but do not present them as a units-based build unless procedure/unit volumes are sourced.
- lesson: Keep currency, unit scale, and exchange-rate assumptions explicit; ranges should reconcile across company-level and top-down estimates.

### U.S., Mexico, & Brazil Resin Compound Market - mixed (Medium quality notes)
The notes reconstruct the base-year market by building regional thermoplastic resin availability/consumption from ACC U.S. production, Mexico production/import volumes, and Brazil plastics-conversion references, then validating flows with UN Comtrade HS Chapter 39 and pricing with ICIS blended resin ASPs. Downstream plastics industry revenue and company references are used as context/validation, not as direct market-size substitutes. The final ~US$37.75 Bn value is described as internally derived from consumption volumes, conversion activity, blended ASP assumptions, and trade-flow validation.
- lesson: Anchor the base year on production/import/consumption volumes from authoritative trade associations or government sources, then use trade data to check regional supply-demand gaps.
- lesson: Convert physical volumes to value with an explicit blended ASP assumption and price benchmark source, rather than treating a third-party market value as the base estimate.
- lesson: Use downstream industry revenue and company references only as triangulation/validation unless they directly measure the target market.

### Global Sodium Oxybate API Market - company revenue roll-up (Low quality notes)
The notes assemble reported net product revenues for finished sodium oxybate products: Jazz Xyrem US$233.8m and high-sodium oxybate authorized-generic royalty revenue US$217.6m in 2024, plus Avadel LUMRYZ US$169.1m in 2024 and 2025 guidance of US$265–275m. They then use Avadel’s cost of products sold (US$15.3m in 2024, about 9% of product revenue; Q2 2025 US$6.37m) to argue that API value is only a fraction of finished-product revenue because API is one part of COGS. Patient counts for Xywav, supplier lists, and DEA quota notes provide context, but the notes do not show an explicit final base-year API market value.
- lesson: For an API market, start from finished-product revenue only as an upper bound, then subtract the much larger commercial/formulation/distribution layer using COGS and margin disclosures.
- lesson: Use public company product-level revenue, royalty, COGS, and patient counts to triangulate, but label every workbook number with units and year because unlabeled intermediate figures cannot support a final base-year value.
- lesson: Regulatory controls such as DEA quota can define supply constraints and participant eligibility, but they do not substitute for API transaction volumes or API-specific price data.

### Global Sodium Oxybate API + FDF Market - company revenue roll-up (Medium quality notes)
The notes support a company-revenue roll-up for the finished-dose layer: they collect Jazz's reported 2024 Xyrem net product sales (US$233.8M) and high-sodium oxybate authorized-generic royalty revenue (US$217.6M), plus Avadel's 2024 LUMRYZ net product revenue (US$169.1M) and COGS (US$15.3M, about 9% of revenue). They then use Avadel's 2025 Q2 revenue/COGS (US$68.1M / US$6.37M) and 2025 guidance (US$265–275M) to argue finished-product margins are high and API value is only a subset of COGS, so total FDF revenue is not the API market. They also note Xywav patient counts and list API/FDF manufacturers, but the notes do not show a complete arithmetic bridge to a single base-year total; the 'Total Market' tokens are not defined.
- lesson: In API+FDF markets, build FDF revenue first, then use COGS and API share-of-COGS to bound API value; do not equate finished-product sales with the API market.
- lesson: Triangulate with product-level revenues, royalties, COGS, and patient counts, but keep units/definitions and an explicit reconciliation to the final total so the sizing is auditable.
- lesson: Check regulatory/quota constraints such as DEA schedule/quota because they can limit API supply and the eligible manufacturer set.

### Global Supply Chain Risk Management Market - unclear (Low quality notes)
The notes show only a segmental share calculation rationale, with unitless coefficients for regions/countries and end-use industries such as United States 0.6824, 0.1007, 0.7645, 0.09732 and GCC Countries 0.5643, 0.1275. They do not show a base-year market value, a source estimate, production/capacity data, trade data, company revenues, installed base, patient epidemiology, per-capita consumption, or units x ASP. Therefore the base-year sizing method cannot be reconstructed from these notes; they support a share-allocation exercise, not an auditable base-year derivation.
- lesson: A segment share tree is not a base-year sizing method unless it is explicitly tied to a sourced base-year total and clearly labeled denominators.
- lesson: Always record the source, base-year value, units, and whether each decimal is a share, growth rate, or index; otherwise the calculation cannot be audited or reused.
- lesson: Keep source evidence separate from segment allocation logic so another analyst can distinguish market-sizing inputs from downstream share calculations.

## Does it generalise? Leave-one-out on unseen markets

Each market is predicted from the others (its own folder excluded). 'gain' = error reduction of the analog prior vs the plain corpus median; 'coverage' = how often the truth fell inside the analog p10-p90 band (~0.8 is calibrated).

| Feature | n | MAE corpus median | MAE analogs | gain | coverage |
|---|---|---|---|---|---|
| growth.fcst_cagr | 72 | 2.7% | 2.5% | +6% | 67% |
| growth.fcst_minus_hist | 72 | +0.25% | +0.24% | +3% | 64% |
| growth.price_cagr | 30 | 0.4% | 0.4% | -1% | 67% |
| region.share.NA | 43 | 6.0% | 5.3% | +12% | 77% |
| region.share.Europe | 43 | 4.2% | 4.2% | +1% | 60% |
| region.share.APAC | 43 | 7.8% | 5.9% | +25% | 67% |
| region.share.LATAM | 43 | 1.6% | 1.8% | -9% | 60% |
| region.share.MEA | 43 | 2.2% | 2.3% | -4% | 63% |
| region.cagr_gap.NA | 43 | +0.73% | +0.72% | +2% | 67% |
| region.cagr_gap.Europe | 43 | +0.72% | +0.70% | +2% | 67% |
| region.cagr_gap.APAC | 43 | +0.86% | +0.87% | -1% | 63% |
| region.cagr_gap.LATAM | 43 | +1.18% | +0.94% | +20% | 63% |
| region.cagr_gap.MEA | 43 | +2.18% | +1.43% | +34% | 58% |
| region.cagr_range | 43 | 1.4% | 1.1% | +22% | 67% |
| country.share.U.S. | 46 | 3.0% | 3.2% | -5% | 63% |
| country.share.Canada | 46 | 3.0% | 3.2% | -5% | 63% |
| country.share.Germany | 47 | 4.7% | 4.3% | +8% | 62% |
| country.share.U.K. | 45 | 4.0% | 4.0% | +2% | 69% |
| country.share.France | 47 | 2.7% | 2.7% | -2% | 55% |
| country.share.Italy | 45 | 2.3% | 2.2% | +4% | 64% |
| country.share.Spain | 45 | 1.9% | 1.8% | +4% | 62% |
| country.share.Russia | 41 | 2.2% | 2.2% | -3% | 54% |
| country.share.China | 42 | 7.7% | 5.4% | +29% | 71% |
| country.share.Japan | 42 | 3.9% | 3.9% | +2% | 60% |
| country.share.India | 42 | 5.0% | 4.2% | +16% | 67% |
| country.share.South Korea | 42 | 1.8% | 1.6% | +14% | 67% |
| country.share.Brazil | 44 | 4.8% | 5.6% | -16% | 64% |
| country.share.Mexico | 44 | 4.8% | 4.2% | +12% | 59% |
| country.share.Argentina | 43 | 3.5% | 3.0% | +13% | 74% |
| country.share.South Africa | 42 | 13.2% | 10.2% | +23% | 67% |
| country.rest_share | 52 | 4.4% | 4.2% | +5% | 71% |
| country.cagr_gap_sd | 53 | +0.45% | +0.42% | +7% | 60% |
| seg.top_share | 72 | 9.0% | 8.7% | +4% | 64% |
| seg.cagr_spread | 72 | 1.2% | 1.2% | -2% | 67% |
| seg.cagr_dev | 72 | 0.5% | 0.4% | +6% | 71% |
| seg.sub_cagr_dev | 36 | 0.6% | 0.6% | +5% | 67% |
| seg.country_mix_sd | 59 | 0.4% | 0.4% | -3% | 64% |
