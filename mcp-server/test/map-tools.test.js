import test from "node:test";
import assert from "node:assert/strict";
import { MAP_TOOL_NAMES, registerMapTools } from "../src/map-tools.js";

function makeHarness() {
  const registered = [];
  const maps = new Map();
  const plans = [];
  const verifications = [];
  let activeMap = null;

  const server = {
    registerTool(name, config, handler) {
      registered.push({ name, config, handler });
    },
  };

  registerMapTools(server, {
    guarded: (handler) => async (args) => handler(args || {}),
    slugify: (text) =>
      String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40),
    isoNow: () => "2026-07-31T00:00:00.000Z",
    writeJsonAtomic: (target, record) => {
      maps.set(target.slice("maps:".length), record);
    },
    mapPath: (slug) => `maps:${slug}`,
    resolveMap: (name) => {
      const slug = name || activeMap;
      const record = maps.get(slug);
      if (!record) {
        return { error: "[FAIL] No active map. Chart one with map_create, or pass a map name." };
      }
      return { slug, record };
    },
    saveMap: (slug, record) => {
      record.updated = "2026-07-31T00:00:00.000Z";
      maps.set(slug, record);
    },
    setActiveMapSlug: (slug) => {
      activeMap = slug;
    },
    clearActiveMapSlug: (slug) => {
      if (slug === null || activeMap === slug) {
        activeMap = null;
      }
    },
    readActiveMapSlug: () => activeMap,
    readJsonl: () => verifications,
    verificationsPath: () => "verifications",
    createPlanRecord: ({ goal, name, steps, source }) => {
      plans.push({ goal, name, steps, source });
      return name;
    },
    attachPlanLineage: (slug, parents) => {
      const plan = plans.find((candidate) => candidate.name === slug);
      plan.lineage = { parents };
    },
    buildDefaultPlanSteps: (horizon) =>
      Array.from({ length: horizon }, (_, index) => ({ title: `default step ${index + 1}` })),
    mcpFrontDoorNote: " Route first.",
  });

  const call = async (name, args) => {
    const entry = registered.find((item) => item.name === name);
    assert.ok(entry, `tool ${name} is not registered`);
    return entry.handler(args);
  };

  return {
    registered,
    maps,
    plans,
    verifications,
    call,
    get activeMap() {
      return activeMap;
    },
  };
}

async function chart(harness) {
  await harness.call("map_create", {
    destination: "Ship a billing revamp spec",
    name: "billing",
    notes: "domain: billing",
    fog: ["how do existing subscriptions migrate"],
  });
}

test("map tool registrar wires every map handler", () => {
  const { registered } = makeHarness();
  assert.deepEqual(
    registered.map((item) => item.name).sort(),
    [...MAP_TOOL_NAMES].sort()
  );
  for (const item of registered) {
    assert.ok(item.config.description.length > 0);
    assert.ok(item.config.title.length > 0);
  }
});

test("map_create records destination, notes, and fog", async () => {
  const harness = makeHarness();
  const text = await chart(harness);
  const record = harness.maps.get("billing");
  assert.equal(record.destination, "Ship a billing revamp spec");
  assert.equal(record.notes, "domain: billing");
  assert.equal(record.status, "charting");
  assert.equal(record.fog.length, 1);
  assert.equal(record.fog[0].id, "F1");
  assert.equal(harness.activeMap, "billing");
  assert.equal(typeof text, "undefined");
});

test("ticket type fixes the human-in-the-loop mode", async () => {
  const harness = makeHarness();
  await chart(harness);
  const expected = { research: "afk", prototype: "hitl", grilling: "hitl", task: "afk" };
  for (const kind of Object.keys(expected)) {
    await harness.call("map_add_ticket", { title: `decide ${kind}`, type: kind });
  }
  const modes = Object.fromEntries(
    harness.maps.get("billing").tickets.map((ticket) => [ticket.type, ticket.mode])
  );
  assert.deepEqual(modes, expected);
});

test("a conversation ticket cannot be downgraded to AFK", async () => {
  const harness = makeHarness();
  await chart(harness);
  const text = await harness.call("map_add_ticket", {
    title: "Pick the proration model",
    type: "grilling",
    mode: "afk",
  });
  assert.match(text, /Mode is fixed for grilling tickets/);
  assert.equal(harness.maps.get("billing").tickets.length, 0);
});

test("blocked tickets stay off the frontier and cannot be claimed", async () => {
  const harness = makeHarness();
  await chart(harness);
  await harness.call("map_add_ticket", { title: "Confirm refunds API", type: "research" });
  await harness.call("map_add_ticket", {
    title: "Provision sandbox",
    type: "task",
    blocked_by: ["T1"],
  });
  const claim = await harness.call("map_claim", { ticket_id: "T2" });
  assert.match(claim, /is blocked by/);
  const status = await harness.call("map_status", {});
  assert.match(status, /Frontier \(1\):/);
  assert.match(status, /Blocked \(1\):/);
});

test("one decision ticket is held at a time but research runs in parallel", async () => {
  const harness = makeHarness();
  await chart(harness);
  await harness.call("map_add_ticket", { title: "Pick the proration model", type: "grilling" });
  await harness.call("map_add_ticket", { title: "Pick the dunning policy", type: "grilling" });
  await harness.call("map_add_ticket", { title: "Confirm refunds API", type: "research" });
  assert.match(await harness.call("map_claim", { ticket_id: "T1" }), /^\[OK\]/);
  assert.match(
    await harness.call("map_claim", { ticket_id: "T2" }),
    /only research tickets run in parallel/
  );
  assert.match(await harness.call("map_claim", { ticket_id: "T3" }), /^\[OK\]/);
});

test("a HITL ticket refuses a self-authored resolution", async () => {
  const harness = makeHarness();
  await chart(harness);
  await harness.call("map_add_ticket", { title: "Pick the proration model", type: "grilling" });
  await harness.call("map_claim", { ticket_id: "T1" });
  const refused = await harness.call("map_resolve", { ticket_id: "T1", answer: "daily proration" });
  assert.match(refused, /Human input required/);
  assert.equal(harness.maps.get("billing").tickets[0].status, "open");

  const resolved = await harness.call("map_resolve", {
    ticket_id: "T1",
    answer: "Daily proration with a monthly true-up",
    gist: "daily proration",
    human_input: "the human chose daily with a monthly true-up",
  });
  assert.match(resolved, /^\[OK\] Resolved ticket Pick the proration model \(T1\)/);
  const record = harness.maps.get("billing");
  assert.equal(record.tickets[0].status, "closed");
  assert.equal(record.tickets[0].claimed_by, "");
  assert.equal(record.decisions[0].gist, "daily proration");
});

test("resolving an unclaimed ticket is refused", async () => {
  const harness = makeHarness();
  await chart(harness);
  await harness.call("map_add_ticket", { title: "Confirm refunds API", type: "research" });
  const refused = await harness.call("map_resolve", { ticket_id: "T1", answer: "covered" });
  assert.match(refused, /is unclaimed/);
});

test("a task ticket with a verifier needs a passing executed run since the claim", async () => {
  const harness = makeHarness();
  await chart(harness);
  harness.verifications.push({
    kind: "executed",
    verified: true,
    exit_code: 0,
    command: "true",
  });
  await harness.call("map_add_ticket", {
    title: "Provision sandbox",
    type: "task",
    verify_command: "true",
  });
  await harness.call("map_claim", { ticket_id: "T1" });
  const refused = await harness.call("map_resolve", { ticket_id: "T1", answer: "provisioned" });
  assert.match(refused, /Verified evidence required/);

  harness.verifications.push({ kind: "executed", verified: false, exit_code: 1, command: "true" });
  assert.match(
    await harness.call("map_resolve", { ticket_id: "T1", answer: "provisioned" }),
    /Verified evidence required/
  );

  harness.verifications.push({ kind: "executed", verified: true, exit_code: 0, command: " true " });
  const resolved = await harness.call("map_resolve", { ticket_id: "T1", answer: "provisioned" });
  assert.match(resolved, /^\[OK\] Resolved ticket/);
  assert.equal(harness.maps.get("billing").tickets[0].verified_command, " true ");
});

test("fog graduates into exactly one ticket", async () => {
  const harness = makeHarness();
  await chart(harness);
  await harness.call("map_add_ticket", {
    title: "Decide the cutover",
    type: "grilling",
    from_fog: "F1",
  });
  assert.equal(harness.maps.get("billing").fog[0].graduated_to, "T1");
  assert.match(
    await harness.call("map_add_ticket", {
      title: "Decide it twice",
      type: "grilling",
      from_fog: "F1",
    }),
    /already graduated/
  );
});

test("out-of-scope work is registered rather than recorded as a decision", async () => {
  const harness = makeHarness();
  await chart(harness);
  await harness.call("map_add_ticket", { title: "Rewrite the invoice renderer", type: "grilling" });
  const text = await harness.call("map_resolve", {
    ticket_id: "T1",
    answer: "the renderer sits past this destination",
    out_of_scope: true,
    human_input: "the human agreed the renderer sits past this destination",
  });
  assert.match(text, /^\[OK\] Ruled ticket/);
  const record = harness.maps.get("billing");
  assert.equal(record.tickets[0].status, "out_of_scope");
  assert.equal(record.decisions.length, 0);
  assert.equal(record.out_of_scope[0].ticket_id, "T1");
});

test("out_of_scope does not bypass the human-input gate on a HITL ticket", async () => {
  const harness = makeHarness();
  await chart(harness);
  await harness.call("map_add_ticket", { title: "Rewrite the invoice renderer", type: "grilling" });
  const refused = await harness.call("map_resolve", {
    ticket_id: "T1",
    answer: "the renderer sits past this destination",
    out_of_scope: true,
  });
  assert.match(refused, /Human input required/);
  assert.equal(harness.maps.get("billing").tickets[0].status, "open");
});

test("the legacy human-input opt-out stamps a durable waiver on the ticket", async () => {
  const harness = makeHarness();
  await chart(harness);
  await harness.call("map_add_ticket", { title: "Pick the proration model", type: "grilling" });
  await harness.call("map_claim", { ticket_id: "T1" });
  process.env.MYTHIFY_REQUIRE_HUMAN_INPUT = "0";
  try {
    const resolved = await harness.call("map_resolve", {
      ticket_id: "T1",
      answer: "daily proration",
    });
    assert.match(resolved, /Human-input gate waived/);
  } finally {
    delete process.env.MYTHIFY_REQUIRE_HUMAN_INPUT;
  }
  const ticket = harness.maps.get("billing").tickets[0];
  assert.equal(ticket.status, "closed");
  assert.equal(ticket.human_input_waived, true);
});

test("map_promote refuses an unclear map and carries decisions into the plan", async () => {
  const harness = makeHarness();
  await chart(harness);
  assert.match(await harness.call("map_promote", {}), /fog patch/);

  await harness.call("map_add_ticket", {
    title: "Decide the cutover",
    type: "grilling",
    from_fog: "F1",
  });
  assert.match(await harness.call("map_promote", {}), /open ticket/);

  await harness.call("map_claim", { ticket_id: "T1" });
  await harness.call("map_resolve", {
    ticket_id: "T1",
    answer: "Cut over in one maintenance window",
    gist: "one maintenance window",
    human_input: "the human picked one window",
    scope_out: ["rewriting the invoice renderer"],
  });
  assert.equal(harness.maps.get("billing").status, "clear");

  const promoted = await harness.call("map_promote", { plan: "billing-build", horizon: 2 });
  assert.match(promoted, /^\[OK\] Promoted map "billing" to plan "billing-build"/);
  const plan = harness.plans[0];
  assert.equal(plan.goal, "Ship a billing revamp spec");
  assert.equal(plan.steps.length, 2);
  assert.equal(plan.source.kind, "map");
  assert.equal(plan.source.decisions[0].gist, "one maintenance window");
  assert.equal(plan.source.out_of_scope[0].note, "rewriting the invoice renderer");
  assert.deepEqual(plan.lineage.parents, [{ kind: "map", id: "billing" }]);
  assert.equal(harness.activeMap, null);
  assert.match(await harness.call("map_promote", { map: "billing" }), /already promoted/);
});

test("map_status reports tickets by name and never mutates", async () => {
  const harness = makeHarness();
  assert.match(await harness.call("map_status", {}), /No active map yet/);
  await chart(harness);
  await harness.call("map_add_ticket", { title: "Pick the proration model", type: "grilling" });
  const before = JSON.stringify(harness.maps.get("billing"));
  const text = await harness.call("map_status", {});
  assert.match(text, /Pick the proration model \(T1\) \[grilling\/hitl\]/);
  assert.match(text, /Not yet specified \(1\):/);
  assert.match(text, /Guardrail: Map tickets record decisions, not executed verification/);
  assert.equal(JSON.stringify(harness.maps.get("billing")), before);
});
