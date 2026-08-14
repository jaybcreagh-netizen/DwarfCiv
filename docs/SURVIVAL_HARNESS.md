# DwarfCiv survival harness

The survival layer gives the governor operational competence without choosing
its values. It describes production dependencies, derives transparent planning
metrics, checks observable preconditions, and preserves projects across
months. The chartered governor still decides which feasible objectives to
pursue and who bears their costs.

## Knowledge policy

`config/survival/handbook.json` is the versioned, machine-readable reference
supplied to the governor. Every governed run freezes that file, with a SHA-256
digest, as `survival-handbook.json` so later analysis can recover the exact
knowledge condition.

The handbook follows three evidence rules:

1. Live observations and structured execution receipts outrank prose.
2. Version-specific mechanics should come from DF raws or DFHack APIs wherever
   possible.
3. Wiki guidance is a planning aid. A procedure remains unverified until a
   live micro-scenario demonstrates its preconditions and postconditions on the
   pinned DF/DFHack version.

The most useful current references are:

- [Dwarf Fortress Wiki quickstart guide](https://new.dwarffortresswiki.org/index.php/Quickstart_guide): the best compact survival sequence covering food, farms, stills, containers, stockpiles, and a trade depot.
- [Dwarf Fortress Wiki food guide](https://new.dwarffortresswiki.org/index.php/Food_guide): production methods, seed preservation, crop timing, farm substrate, and labor bottlenecks.
- [Dwarf Fortress Wiki trading guide](https://new.dwarffortresswiki.org/index.php/Trading): depot, accessibility, hauling, trader, caravan, and transaction dependencies.
- [DFHack quickfort](https://docs.dfhack.org/en/latest/docs/tools/quickfort.html): programmatic and dry-runnable digging, buildings, stockpiles, zones, and blueprint material orders.
- [DFHack quickfort blueprint guide](https://docs.dfhack.org/en/stable/docs/guides/quickfort-user-guide.html): current hospital-location syntax, resident access, desired medical supplies, and furniture symbols.
- [DFHack buildingplan](https://docs.dfhack.org/en/stable/docs/tools/buildingplan.html): planned furniture, material matching, manager-order generation, and completion state.
- [Dwarf Fortress Wiki military quickstart](https://new.dwarffortresswiki.org/index.php/Military_quickstart): current squad, equipment, barracks, scheduling, and civilian-work contention guidance.
- [DFHack autofarm](https://docs.dfhack.org/en/stable/docs/tools/autofarm.html): a reference implementation for assigning crops from stock thresholds and available seeds.
- [DFHack seedwatch](https://docs.dfhack.org/en/stable/docs/tools/seedwatch.html): a reference implementation for protecting the seed base from cooking.
- [DFHack orders](https://docs.dfhack.org/en/stable/docs/tools/orders.html): manager-order inspection, rechecking, and cancellation-spam recovery.
- [DFHack gui/pathable](https://docs.dfhack.org/en/stable/docs/tools/gui/pathable.html): native wagon/pack-animal access from a depot to the map edge.
- [DFHack caravan](https://docs.dfhack.org/en/stable/docs/tools/caravan.html): current caravan state and move-goods semantics. Governed runs do not use its diagnostic commands to extend, revive, or pacify caravans.

Older versioned wiki pages are useful historical context but should not be
copied into the handbook without live verification against v53.

## Current operational loop

Each month:

1. `obs-state.lua` records living citizens, stocks, workshops, farms,
   farm construction jobs, seasonal crop assignments, seed types and
   protection targets, stockpiles, zones, manager orders, designations,
   threats, and supported labor flags.
2. `harness.survival` computes per-capita stocks, estimated food/drink runway,
   stock deltas, risk bands, urgent findings, and procedure feasibility.
3. The model receives the charter, briefing, survival context, latest strategy
   record, recent account entries, and bounded tool schemas.
4. The model returns both actions and a structured multi-horizon strategy.
5. Actions execute through the single dispatcher and produce
   `applied|no_effect|failed` receipts.
6. A fresh DF observation and the receipts are stored in `strategy.jsonl`.
7. Only then does the separate diary call narrate what happened.

This keeps three claims distinct: a procedure was feasible, an action was
accepted by DF, and production later completed.

## Implemented survival procedures

- Forage visible plants.
- Brew observed plants when a still and empty barrel are available.
- Process raw fish at a completed fishery.
- Assign the distinct `FISH` and `CLEAN_FISH` labors.
- Observe seed counts, surface/subterranean suitability, legal seasons, grow
  duration, brewability, and relevant persistent seedwatch targets.
- Designate a bounded shallow farm room and access stairs from visible surface
  facts without inspecting the hidden layer for favorable geology.
- Persist and observe that room as `digging`, `ready`, `blocked`, or
  `farm_built`, including hidden, active-designation, and suitable-tile counts.
- Designate a bounded farm plot only on visible compatible soil or mud.
- Inspect and prioritize one exact unclaimed farm construction job.
- Assign only an observed seed-backed, environment-compatible crop to legal
  seasons on a completed plot.
- Protect a crop's seed base with a native target that remains observable even
  when the free seed count reaches zero.
- Construct a carpenter, still, or fishery on a bounded verified footprint;
  inspect and prioritize its exact construction job.
- Construct a craftsdwarf workshop and native 5x5 trade depot from exact
  reachable logs; keep designation, completion, citizen access, and native
  wagon/pack-animal pathability as separate claims.
- Observe and deliberately fill the native manager and broker offices without
  silently replacing an existing holder.
- Queue wood-bound `MakeCrafts` orders only after a manager exists, select a
  bounded set of nonessential finished-good ids, and create exact native
  `BringItemToDepot` jobs only while a caravan is active.
- Separate fortress exports, merchant cargo, and permanent depot construction
  materials; request the appointed trader without treating the request as
  evidence that the trader arrived.
- Queue a wood-constrained barrel order when a completed carpenter workshop
  and available log are observed.
- Observe exact workshop production jobs, their source order ids, and tracked
  barrel, drink, raw-fish, and prepared-fish item ids.
- Create named, typed food/seed/plant/booze/wood/refuse stockpiles on bounded
  reachable footprints; require refuse piles to be outdoors; and observe each
  pile's categories, container reservations, exposure, reachability, and
  current contents.
- Cancel one exact impossible manager order, rejecting unknown ids or orders
  with dependants.
- Designate and observe a bounded protected hospital room without revealing
  hidden geology; repair its visible ramp access and keep mining work details
  coherent with direct labor flags.
- Create and reconcile exact native hospital zone/location ids from their
  footprint, including across save/reload boundaries where auxiliary project
  metadata can be absent.
- Observe completed versus planned beds, tables, containers, exact available
  furniture items, hospital supply demand, patients, clinical jobs, doctor
  candidates, and location occupations.
- Construct a carpenter dependency, queue bounded wood-specific bed/table/
  chest orders, designate the three furnishings, and count them as capacity
  only after their native build stages complete.
- Assign one exact living citizen to the native all-purpose doctor occupation,
  verifying both location and world indices and preserving scarce mining,
  woodcutting, fishing, manager, and broker roles when medical skill is tied.
- Persist immediate, tactical, seasonal, and strategic projects separately
  from the diary.

## Live farming acceptance

The deterministic `agent.farm_recovery_governor` is an executable acceptance
controller, not an experimental research governor. Two live paths have been
demonstrated on the pinned runtime.

The older-pilot surface path designated a native 3x3 plot, diagnosed its
unclaimed construction job, reserved the actual Planter, prioritized only that
job, observed stage 3/3, assigned KANIWA to all four seasons, and reloaded a
later snapshot with seed protection still visible at 0/10.

The stronger canonical-embark path starts from `saves/dwarfciv-start` under the
normal scarcity setup and demonstrates the complete dependency chain:

1. select year-round brewable plump helmets from the observed subterranean
   seed stock and protect them at 10;
2. let farm placement fail because no suitable subterranean rectangle is yet
   visible, recording the failed receipt instead of claiming a farm;
3. select a surface entry without inspecting hidden geology, designate 28
   high-priority access/room tiles, and reserve the fort's Miner;
4. observe the project first as `digging` with 25/25 room tiles hidden, then as
   `ready` with 25/25 visible suitable soil tiles;
5. designate farm id 3, observe it complete at stage 3/3, and assign plump
   helmets to spring, summer, autumn, and winter; and
6. observe food/plants rise from 14/0 before crop assignment to 23/8 one month
   later, then take only no-op actions while the farm and project linkage
   persist across snapshots.

The live evidence is in
`runs/harness-v2-underground-farm-canonical-20260814/`. This is a mechanism
acceptance result, not proof that one 3x3 plot can sustain every future
population or that drink production is solved.

The acceptance controller deliberately makes stronger labor choices than a
research governor. Every labor change is individually receipted and
reversible; it exists to prove the mechanism and expose bottlenecks.

## Live workshop and food-chain acceptance

Three additional deterministic micro-scenarios now use physical item identity,
not manager-order creation or ambiguous stock counts, as their postcondition:

- `runs/harness-v2-container-canonical-v4-20260814/` designates two trees,
  observes available logs, constructs carpenter shop id 3, queues a
  wood-constrained `MakeBarrel` order, and observes available wood fall 11→10
  while new barrel item id 867 appears. The order completes and disappears.
- `runs/harness-v2-brewing-canonical-20260814/` queues one verified
  `BREW_DRINK_FROM_PLANT` reaction, reserves a brewer, and observes new drink
  item id 681, drink stock rise 7→10, and available brewable plants fall 7→5.
- `runs/harness-v2-fish-canonical-20260814/` distinguishes catching (`FISH`)
  from processing (`CLEAN_FISH`). Native local fishery jobs process catches
  before the monthly boundary: seven new prepared-fish item ids appear and
  food rises 7→27 without a manager order. The handbook therefore teaches
  automatic fishery processing first and exposes `prepare_fish` only for
  available raw fish that remains unclaimed.

The earlier container attempts are retained as negative evidence. They showed
that manually forcing manager status flags and leaving `MakeBarrel` material
unspecified can produce a validated-looking order with no workshop job. The
actions now leave status transitions to DF and constrain barrel orders to wood.

`runs/harness-v3-logistics-canonical-20260814/` adds the first logistics
acceptance: booze pile id 3 is created adjacent to still id 1 with the `booze`
preset; refuse pile id 4 has the refuse category and 9/9 outdoor tiles. Both
are reachable from the reference citizen, all observed plants and empty food
containers are reachable, and no observer errors occur. Stockpile creation is
still only routing intent; the observer now reports contents so subsequent
runs can separately verify hauling throughput.

That separate throughput check is
`runs/harness-v3-logistics-hauling-20260814/`: under the unmodified baseline,
the new booze pile starts with zero item records and, one month later, contains
9 barrels and 41 drink units. Total drink falls 60→53 as citizens consume it,
so the pile contents—not the global stock total—provide the hauling evidence.

## Live seasonal-trade acceptance

The trade tranche is a dependency chain, not a single `trade` abstraction.

- `runs/harness-v4-trade-depot-20260814/` constructs depot id 1 from three
  exact logs. It reaches stage 3/3, remains citizen-reachable, and the native
  pathability plugin reports wagon and pack-animal access.
- `runs/harness-v4-trade-hauling-20260814/` is negative evidence: a
  wood-bound `MakeCrafts` order remained unvalidated because the baseline
  fort had no manager. The scarcity fixture had hidden this institutional
  dependency by appointing one during setup. Merchant cargo also exposed why
  temporary depot contents must be separated by ownership.
- `runs/harness-v5-trade-manager-hauling-20260814/` appoints unit 210 as the
  native manager, after which the stalled order completes and creates five
  chestnut craft ids. Five exact `BringItemToDepot` jobs later place the same
  ids in depot 1 as fortress exports worth 88; 210 merchant item records are
  reported separately.
- `runs/harness-v6-trade-broker-v2-20260814/` appoints and verifies the broker,
  but demonstrates that directly writing `trader_requested=true` is
  insufficient: no `TradeAtDepot` job appears. The disposable native-UI
  comparison in `runs/trade-ui-native-request-20260814/` clicks DF's rendered
  **Broker requested at depot** control and immediately creates unsuspended
  `TradeAtDepot` job 227. `request_trader` now uses that native path and
  requires the job id. Worker arrival and transaction execution remain
  separate postconditions.
- `runs/trade-ui-anyone-request-20260814/` changes that same live depot through
  DF's rendered **Anyone requested at depot** control. The observer records
  native request mode 3, and unit 204 takes job 227 within one in-game day
  while the appointed broker remains asleep. The controller therefore uses
  anyone mode only as an explicit survival fallback when the caravan has 35
  days or fewer remaining or the fortress has no drink.
- `runs/harness-v10-anyone-trader-worker-20260814/` exercises that fallback
  through the governed action path. At 31 caravan days and zero drink, the
  receipt records `native_trader_mode_changed` for depot 1 and job 227. One
  month later the observer reports request mode `anyone`, unit 205 working the
  job, all five fortress exports at the depot, and merchant cargo classified
  separately. This proves staffing, not an exchange; itemized transaction
  execution remains the next trade acceptance gate.
- `runs/harness-v11-trade-import-observer-20260814/` identifies exact
  merchant-owned survival candidates without advancing time, including item
  ids, descriptions, stack sizes, values, and roles such as food, container,
  bucket, and medical thread.
- `runs/harness-v12k-itemized-trade-20260814/` completes the native exchange.
  The action selects five exact exports worth 88 and one five-fish import
  worth 10, renders DF's totals, clicks **Trade**, accepts the separate native
  confirmation, and verifies ownership changed for every id. The independent
  post-action observer sees import 1345 as fortress goods and food rise from
  21 to 26; the five exports move to merchant cargo. This is the first complete
  causal trade receipt rather than a request, job, or UI-state proxy.

Early hauling jobs created before a caravan existed disappeared from DF's job
list. `mark_goods_for_trade` now requires an active caravan, matching the
current move-goods window and preventing blind monthly retries.

## Live healthcare acceptance

The healthcare tranche now reaches a physically furnished and staffed native
hospital, while retaining the distinction between capacity and delivered
treatment.

- `runs/harness-v14-hospital-room-zone-20260814/` through
  `runs/harness-v16-hospital-native-miner-20260814/` are useful negative and
  intermediate evidence. They showed that the room observer initially ignored
  hidden dig designations, that a surface channel can disappear until DF
  assigns a native miner work detail and pick, and that validating an activity
  zone by the wrong building enum can report failure after DF has actually
  created the zone and hospital. In v16, unit 204 takes iron pick 117, creates
  the ramp, and mines all 49 room tiles. Post-action observation then finds
  native zone 3 linked to hospital location 0, exposing the validator error.
- `runs/harness-v17-hospital-zone-reconcile-20260814/` validates exact native
  footprint reconciliation and fixes activity-zone counting. Because location
  id 0 is valid and some auxiliary site data did not survive snapshot copying,
  observer and actions now infer missing receipt ids from the exact linked
  native footprint; they never create a duplicate merely because metadata is
  absent.
- `runs/harness-v20-hospital-furnishings-idzero-20260814/` completes the
  furniture chain. Carpenter workshop 4 reaches stage 3/3; wood-bound native
  orders 0, 1, and 2 produce one bed, table, and chest; quickfort designates
  building ids 7, 5, and 6; and the observer keeps all three under `planned`
  at stage 0 before moving them to completed capacity at stage 1. The hospital
  chest begins receiving existing medical supplies, but the observer still
  reports deficits in crutches, splints, plaster, and soap.
- `runs/harness-v21-hospital-doctor-20260814/` creates native all-purpose
  doctor occupation 2 and proves it survives a month and save. That run also
  catches an unsafe tie-break: with every citizen at medical skill zero, id
  order selected the only miner. The ranking was corrected before canonical
  acceptance.
- `runs/harness-v22-doctor-bottleneck-aware-20260814/` is the corrected path:
  equally unskilled candidate 207 is selected with burden 0, preserving the
  assigned miner, woodcutter, fisher, manager, and broker. The receipt verifies
  the occupation in both native indices and the independent observer reports
  the same living doctor at location 0.

This proves protected space, native location linkage, furniture manufacture
and installation, partial supply logistics, and clinical staffing. It does not
yet prove recovery, diagnosis, surgery, dressing, or patient outcome. Triage
and medical-priority policies remain unavailable until a bounded patient
micro-scenario produces and completes exact native medical jobs.

## Live clean-water acceptance

The water tranche is deliberately split into a component chain, which is
live-verified, and a siting chain, which this embark cannot satisfy.

The first observer was wrong in a way worth recording. `tile_designation`
exposes `liquid_type` as a **boolean** in the pinned DFHack build (`false` =
water, `true` = magma), not the `tile_liquid` enum. Comparing against
`df.tile_liquid.Water` therefore matched nothing and
`runs/harness-v23-water-observer-20260814/` reported a completely dry map.
An independent probe found 101,394 liquid tiles. The corrected observer in
`runs/harness-v23b-water-observer-fixed-20260814/` reports the same numbers
as the probe: 109 visible water tiles, all stagnant, with 101,285 hidden
water tiles that the observer deliberately does not reveal. A silent zero
that looks like a fact about the map is exactly the failure the evidence
rules exist to catch, so all water checks now accept either representation.

- `runs/harness-v24-water-components-20260814/` and
  `runs/harness-v24b-water-components-20260814/` are negative evidence.
  A mason's workshop completed and four `ConstructBlocks` orders were
  created, but every one sat at `validated=false` with `mat_type=-1` and no
  workshop job ever appeared. Mason labor was enabled on eleven citizens
  and three reachable boulders existed, so neither labor nor material
  availability explained it. An unbound stone order is the same silent
  failure the underspecified `MakeBarrel` once produced. These runs also
  exposed a controller fault: it re-queued the order every month instead of
  asking why the outstanding one had produced nothing.
- `runs/harness-v25-water-material-bound-20260814/` is the corrected path.
  Stone orders now carry the exact material token of an observed available
  boulder (`INORGANIC:LIGNITE`). Block items 3029, 3030, 3031, and 3032
  appear one month after the order, and mechanism item 3162 appears after
  the mechanic's workshop completes. The controller then holds instead of
  re-queuing while an order is pending.
- `runs/harness-v26-water-stagnant-fallback-20260814/` establishes the
  boundary. With every component in hand, no well can be sited: the map
  offers no visible fresh water, and none of the 109 stagnant tiles has an
  adjacent walkable tile reachable from a citizen. The controller takes no
  action and records the reason. Digging toward the hidden water would both
  reveal geology the governor is not entitled to see and manufacture a
  resource the map did not provide.

So the component chain — mason and mechanic workshops, material-bound stone
orders, and physical block and mechanism items — is live-verified, while
`prepare_well_site`, `build_well`, and `designate_water_source` remain
implemented and schema-backed but unexecuted. `establish_clean_water` stays
`unverified` and reports `unavailable` to the governor.

Because the handbook's own recovery text allows a stagnant fallback,
`designate_water_source` takes an explicit `allow_stagnant` argument rather
than silently substituting poor water. The receipt records water quality,
whether the water is clean, and whether infection risk was accepted. Water
depth also fluctuates across months — one observation recorded zero visible
stagnant tiles where neighbouring months recorded 109 — so a single
observation is not evidence of a permanent water supply.

## Next implementation sequence

1. **Farm-room recovery:** if the first epistemically honest shallow room
   reveals stone, dampness, or an aquifer, expose a bounded alternate-room or
   safe-irrigation plan. The current controller stops at `blocked`; it never
   searches hidden tiles or manufactures mud.
2. **Starter construction skills:** add basic bedrooms and a consolidated food
   district. Refuse handling, the trade depot, and a basic hospital are now
   implemented and live-tested.
3. **Rock-pot alternative:** add and physically verify a stone container chain
   so brewing is not dependent on scarce wood.
4. **Logistics:** add input/output links and container-reservation tuning on
   top of the live-tested typed stockpiles and reachability/content observer.
5. **Trade:** broaden import classification to contained liquids and seeds,
   and add a longer seasonal regression. The end-to-end first exchange—from
   depot construction through an itemized, ethics-aware ownership receipt—is
   implemented and live verified.
6. **Health and defence:** the `injury` scenario and
   `agent.treatment_recovery_governor` are implemented but not yet run
   live, so `validate_clinical_treatment` remains `missing_tool`. The
   clean-water component chain is verified; well siting needs an embark
   with reachable surface water. Defence is still unimplemented: doors and
   bridges, a safe civilian burrow and alert, equipment-aware squads,
   barracks, schedules, and training. Only expose triage or lockdown
   choices after their causal receipts exist.
7. **Evaluation:** micro-scenarios first, then 3-, 6-, and 12-month survival
   gates before another 24-month model reign.

Continuous DFHack automation such as `autofarm` should be a separately labelled
baseline or a mechanism whose targets are chosen by the governor. Enabling an
unobserved autopilot during a moral-governance experiment would transfer policy
decisions away from the model and confound attribution.
