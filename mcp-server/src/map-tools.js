import fs from "node:fs";
import { z } from "zod";
import { noopVerifierReason } from "./evidence-guard.js";

export const MAP_TOOL_NAMES = [
  "map_create",
  "map_add_ticket",
  "map_claim",
  "map_resolve",
  "map_status",
  "map_promote",
];

export const MAP_TICKET_TYPES = ["research", "prototype", "grilling", "task"];
export const MAP_TICKET_MODES = ["afk", "hitl"];
// Wayfinding fixes the mode per type so a session cannot quietly downgrade a
// human conversation into something it answers by itself. Only `task` is
// genuinely either, so only `task` accepts an explicit mode.
export const MAP_TYPE_MODES = {
  research: "afk",
  prototype: "hitl",
  grilling: "hitl",
  task: "afk",
};
export const MAP_GUARDRAIL =
  "Map tickets record decisions, not executed verification. A resolved ticket clears a question; " +
  "completion still requires a passing executed check.";
export const MAP_HUMAN_INPUT_MESSAGE =
  "[FAIL] Human input required: this is a HITL ticket, so the agent cannot resolve it from its own words. " +
  "Hold the conversation, then pass human_input with what the human actually decided. " +
  "Set MYTHIFY_REQUIRE_HUMAN_INPUT=0 only for legacy self-resolved tickets.";
export const MAP_HUMAN_INPUT_WAIVED_WARNING =
  "[WARN] Human-input gate waived: MYTHIFY_REQUIRE_HUMAN_INPUT=0 is set, so this HITL ticket " +
  "resolved without the human's words. The waiver is stamped on the ticket as human_input_waived.";

const FALSE_ENV_VALUES = new Set(["0", "false", "no", "off"]);

function requireDep(deps, name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`registerMapTools requires deps.${name}`);
  }
  return value;
}

export function nextMapId(items, prefix) {
  let highest = 0;
  for (const item of items || []) {
    const raw = String((item && item.id) || "");
    if (raw.startsWith(prefix)) {
      const parsed = Number.parseInt(raw.slice(prefix.length), 10);
      if (Number.isInteger(parsed) && parsed > highest) {
        highest = parsed;
      }
    }
  }
  return `${prefix}${highest + 1}`;
}

export function findTicket(record, ticketId) {
  const wanted = String(ticketId || "").trim().toUpperCase();
  return (record.tickets || []).find((t) => String(t.id || "").toUpperCase() === wanted) || null;
}

export function openTickets(record) {
  return (record.tickets || []).filter((t) => t.status === "open");
}

export function ticketBlockers(record, ticket) {
  const blockers = [];
  for (const blockerId of ticket.blocked_by || []) {
    const blocker = findTicket(record, blockerId);
    if (blocker && blocker.status === "open") {
      blockers.push(blocker);
    }
  }
  return blockers;
}

export function frontierTickets(record) {
  return openTickets(record).filter(
    (ticket) => ticketBlockers(record, ticket).length === 0 && !ticket.claimed_by
  );
}

export function ungraduatedFog(record) {
  return (record.fog || []).filter((item) => !item.graduated_to);
}

export function mapIsClear(record) {
  return openTickets(record).length === 0 && ungraduatedFog(record).length === 0;
}

// Refer by name. The id rides inside the name; it never stands in for it.
export function ticketName(ticket) {
  return `${ticket.title || ""} (${ticket.id || ""})`;
}

export function requireHumanInputEnabled() {
  const raw = String(process.env.MYTHIFY_REQUIRE_HUMAN_INPUT || "").trim().toLowerCase();
  return !FALSE_ENV_VALUES.has(raw);
}

export function normalizedCommand(text) {
  return String(text || "").split(/\s+/).filter(Boolean).join(" ");
}

export function ticketLine(record, ticket) {
  let line = `- ${ticketName(ticket)} [${ticket.type || ""}/${ticket.mode || ""}]`;
  if (ticket.claimed_by) {
    line += ` claimed by ${ticket.claimed_by}`;
  }
  const blockers = ticketBlockers(record, ticket);
  if (blockers.length > 0) {
    line += ` blocked by: ${blockers.map(ticketName).join(", ")}`;
  }
  return line;
}

export function mapNextAction(record) {
  if (record.status === "promoted") {
    return "This map is promoted; work its plan with plan_update_step and verify_run.";
  }
  if (mapIsClear(record)) {
    return "The way is clear. Run map_promote to hand the destination to a plan.";
  }
  const claimed = openTickets(record).filter((t) => t.claimed_by);
  if (claimed.length > 0) {
    return `Resolve the claimed ticket ${ticketName(claimed[0])} with map_resolve.`;
  }
  const frontier = frontierTickets(record);
  if (frontier.length > 0) {
    return `Claim the next frontier ticket ${ticketName(frontier[0])} with map_claim.`;
  }
  if (openTickets(record).length > 0) {
    return "Every open ticket is blocked. Resolve a blocker, or add a ticket that unblocks one.";
  }
  return "Graduate a fog patch into a ticket with map_add_ticket and from_fog.";
}

export function formatMap(slug, record) {
  const frontier = frontierTickets(record);
  const claimed = openTickets(record).filter((t) => t.claimed_by);
  const blocked = openTickets(record).filter(
    (t) => ticketBlockers(record, t).length > 0 && !t.claimed_by
  );
  const fog = ungraduatedFog(record);
  const outOfScope = record.out_of_scope || [];
  const decisions = record.decisions || [];
  const lines = [
    `[OK] Map "${slug}": ${record.destination || ""}`,
    `Status: ${record.status || "charting"}`,
    `Tickets: ${(record.tickets || []).length} total, ${openTickets(record).length} open, ${frontier.length} on the frontier`,
  ];
  if (record.notes) {
    lines.push(`Notes: ${record.notes}`);
  }
  lines.push(`Decisions so far (${decisions.length}):`);
  if (decisions.length === 0) {
    lines.push("- none");
  }
  for (const decision of decisions) {
    lines.push(`- ${decision.title || ""} (${decision.ticket_id || ""}) - ${decision.gist || ""}`);
  }
  lines.push(`Frontier (${frontier.length}):`);
  if (frontier.length === 0) {
    lines.push("- none");
  }
  for (const ticket of frontier) {
    lines.push(ticketLine(record, ticket));
  }
  if (claimed.length > 0) {
    lines.push(`Claimed (${claimed.length}):`);
    for (const ticket of claimed) {
      lines.push(ticketLine(record, ticket));
    }
  }
  if (blocked.length > 0) {
    lines.push(`Blocked (${blocked.length}):`);
    for (const ticket of blocked) {
      lines.push(ticketLine(record, ticket));
    }
  }
  lines.push(`Not yet specified (${fog.length}):`);
  if (fog.length === 0) {
    lines.push("- none");
  }
  for (const item of fog) {
    lines.push(`- ${item.id}: ${item.note || ""}`);
  }
  lines.push(`Out of scope (${outOfScope.length}):`);
  if (outOfScope.length === 0) {
    lines.push("- none");
  }
  for (const item of outOfScope) {
    let text = `- ${item.id}: ${item.note || ""}`;
    if (item.reason) {
      text += ` (why: ${item.reason})`;
    }
    lines.push(text);
  }
  lines.push(`Next action: ${mapNextAction(record)}`);
  lines.push(`Guardrail: ${MAP_GUARDRAIL}`);
  return lines.join("\n");
}

export function registerMapTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const slugify = requireDep(deps, "slugify");
  const isoNow = requireDep(deps, "isoNow");
  const writeJsonAtomic = requireDep(deps, "writeJsonAtomic");
  const mapPath = requireDep(deps, "mapPath");
  const resolveMap = requireDep(deps, "resolveMap");
  const saveMap = requireDep(deps, "saveMap");
  const setActiveMapSlug = requireDep(deps, "setActiveMapSlug");
  const clearActiveMapSlug = requireDep(deps, "clearActiveMapSlug");
  const readActiveMapSlug = requireDep(deps, "readActiveMapSlug");
  const readJsonl = requireDep(deps, "readJsonl");
  const verificationsPath = requireDep(deps, "verificationsPath");
  const createPlanRecord = requireDep(deps, "createPlanRecord");
  const buildDefaultPlanSteps = requireDep(deps, "buildDefaultPlanSteps");
  const frontDoorNote = typeof deps.mcpFrontDoorNote === "string" ? deps.mcpFrontDoorNote : "";

  function uniqueMapSlug(base) {
    let slug = base;
    let suffix = 2;
    while (fs.existsSync(mapPath(slug))) {
      slug = `${base.slice(0, 36)}-${suffix}`;
      suffix += 1;
    }
    return slug;
  }

  server.registerTool(
    "map_create",
    {
      title: "Chart a wayfinding decision map",
      description:
        "Create a decision map with a destination, optional notes, and any fog you can already see, then set it active. " +
        "Use this when an effort is too big for one session and the route to the destination is not visible yet: the map " +
        "holds the decisions to make before anyone plans execution." +
        frontDoorNote,
      inputSchema: {
        destination: z.string().describe("What reaching the end of this map looks like; one or two lines."),
        name: z.string().optional().describe("Map name; slugified for the filename. Defaults to a slug of the destination."),
        notes: z.string().optional().describe("Domain, skills, and standing preferences for this effort."),
        fog: z
          .array(z.string())
          .optional()
          .describe("Questions you can see coming but cannot yet state sharply enough to ticket."),
      },
    },
    guarded(({ destination, name, notes, fog }) => {
      const base =
        slugify(name !== undefined && name !== null && String(name).trim() !== "" ? name : destination) ||
        "map";
      const slug = uniqueMapSlug(base);
      const now = isoNow();
      const fogItems = [];
      for (const note of fog || []) {
        fogItems.push({ id: nextMapId(fogItems, "F"), note, graduated_to: "", created: now });
      }
      const record = {
        id: slug,
        destination,
        notes: notes || "",
        status: "charting",
        tickets: [],
        fog: fogItems,
        out_of_scope: [],
        decisions: [],
        created: now,
        updated: now,
      };
      writeJsonAtomic(mapPath(slug), record);
      setActiveMapSlug(slug);
      return [
        `[OK] Created map "${slug}"; it is now the active map.`,
        `Destination: ${destination}`,
        `Not yet specified: ${fogItems.length}`,
        "Next: add the decisions you can already state with map_add_ticket.",
      ].join("\n");
    })
  );

  server.registerTool(
    "map_add_ticket",
    {
      title: "Add a decision ticket to a map",
      description:
        "Add a decision ticket to the named or active map. The type fixes whether a human is in the loop: research and " +
        "task run AFK, prototype and grilling are HITL, and only task tickets may choose their mode. " +
        "Use this while charting, and again whenever a resolution makes a new question sharp enough to state.",
      inputSchema: {
        title: z.string().describe("Ticket title. This is its name; reports refer to it by name."),
        type: z.enum(MAP_TICKET_TYPES).describe("research, prototype, grilling, or task."),
        question: z.string().optional().describe("The decision this ticket resolves. Defaults to the title."),
        mode: z
          .enum(MAP_TICKET_MODES)
          .optional()
          .describe("Only for task tickets: afk when the agent can drive it alone, hitl otherwise."),
        blocked_by: z.array(z.string()).optional().describe("Ticket ids this ticket waits on."),
        verify_command: z
          .string()
          .optional()
          .describe("Executable proof of a task ticket's done-condition; run it with the CLI map verify."),
        from_fog: z.string().optional().describe("Fog patch id this ticket graduates."),
        map: z.string().optional().describe("Map name; omit to use the active map."),
      },
    },
    guarded((input) => {
      const resolved = resolveMap(input.map);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, record } = resolved;
      let mode = MAP_TYPE_MODES[input.type];
      if (input.mode) {
        if (input.type !== "task") {
          return (
            `[FAIL] Mode is fixed for ${input.type} tickets (${mode}). ` +
            "Only task tickets choose between afk and hitl."
          );
        }
        mode = input.mode;
      }
      const blockedBy = [];
      for (const raw of input.blocked_by || []) {
        for (const part of String(raw).split(",")) {
          const candidate = part.trim().toUpperCase();
          if (!candidate) {
            continue;
          }
          if (!findTicket(record, candidate)) {
            return `[FAIL] Blocking ticket not found in map "${slug}": ${candidate}`;
          }
          if (!blockedBy.includes(candidate)) {
            blockedBy.push(candidate);
          }
        }
      }
      let fogItem = null;
      if (input.from_fog) {
        const wanted = String(input.from_fog).trim().toUpperCase();
        fogItem = (record.fog || []).find((item) => String(item.id || "").toUpperCase() === wanted) || null;
        if (!fogItem) {
          return `[FAIL] Fog patch not found in map "${slug}": ${input.from_fog}`;
        }
        if (fogItem.graduated_to) {
          return `[FAIL] Fog patch ${fogItem.id} already graduated into ${fogItem.graduated_to}.`;
        }
      }
      const ticket = {
        id: nextMapId(record.tickets || [], "T"),
        title: input.title,
        question: input.question || input.title,
        type: input.type,
        mode,
        status: "open",
        blocked_by: blockedBy,
        claimed_by: "",
        claimed_at: "",
        resolution: "",
        human_input: "",
        resolved_at: "",
        created: isoNow(),
      };
      const verifyCommand = String(input.verify_command || "").trim();
      if (verifyCommand) {
        ticket.verify_command = verifyCommand;
      }
      if (!Array.isArray(record.tickets)) {
        record.tickets = [];
      }
      record.tickets.push(ticket);
      if (fogItem) {
        fogItem.graduated_to = ticket.id;
      }
      saveMap(slug, record);
      const lines = [
        `[OK] Added ticket ${ticketName(ticket)} to map "${slug}"`,
        `Type: ${ticket.type} (${ticket.mode})`,
      ];
      if (blockedBy.length > 0) {
        lines.push(`Blocked by: ${blockedBy.join(", ")}`);
      }
      if (fogItem) {
        lines.push(`Graduated fog patch ${fogItem.id}.`);
      }
      if (ticket.mode === "hitl") {
        lines.push(
          "This ticket needs a human. Resolving it requires human_input; the agent must not answer its own question."
        );
      }
      if (verifyCommand) {
        const noopReason = noopVerifierReason(verifyCommand);
        if (noopReason) {
          lines.push(
            `[WARN] Ticket verify command looks like a no-op (${noopReason}): ${verifyCommand}. ` +
              "It will satisfy the resolution gate without checking anything."
          );
        }
      }
      return lines.join("\n");
    })
  );

  server.registerTool(
    "map_claim",
    {
      title: "Claim a frontier ticket",
      description:
        "Claim an open, unblocked ticket before any work on it, so concurrent sessions skip it. " +
        "One decision ticket is held at a time; only research tickets run in parallel. " +
        "Use this first, every time, before resolving anything.",
      inputSchema: {
        ticket_id: z.string().describe("Ticket id such as T1."),
        by: z.string().optional().describe("Claimant. Defaults to MYTHIFY_MAP_CLAIMANT or session."),
        map: z.string().optional().describe("Map name; omit to use the active map."),
      },
    },
    guarded(({ ticket_id, by, map: mapName }) => {
      const resolved = resolveMap(mapName);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, record } = resolved;
      const ticket = findTicket(record, ticket_id);
      if (!ticket) {
        return `[FAIL] Ticket ${ticket_id} not found in map "${slug}".`;
      }
      if (ticket.status !== "open") {
        return `[FAIL] Ticket ${ticketName(ticket)} is ${ticket.status}, not open.`;
      }
      const blockers = ticketBlockers(record, ticket);
      if (blockers.length > 0) {
        return (
          `[FAIL] Ticket ${ticketName(ticket)} is blocked by: ` +
          `${blockers.map(ticketName).join(", ")}. Resolve the blockers first.`
        );
      }
      const claimant =
        String(by || process.env.MYTHIFY_MAP_CLAIMANT || "session").trim() || "session";
      if (ticket.claimed_by) {
        if (ticket.claimed_by === claimant) {
          return `[WARN] Ticket ${ticketName(ticket)} is already claimed by ${claimant}.`;
        }
        return `[FAIL] Ticket ${ticketName(ticket)} is already claimed by ${ticket.claimed_by}.`;
      }
      if (ticket.type !== "research") {
        const held = openTickets(record).filter(
          (other) => other.claimed_by === claimant && other.type !== "research"
        );
        if (held.length > 0) {
          return (
            `[FAIL] ${claimant} already holds ${ticketName(held[0])}. Resolve it before ` +
            "claiming another decision ticket; only research tickets run in parallel."
          );
        }
      }
      ticket.claimed_by = claimant;
      ticket.claimed_at = isoNow();
      ticket.verification_cursor = readJsonl(verificationsPath()).length;
      saveMap(slug, record);
      const lines = [
        `[OK] Claimed ticket ${ticketName(ticket)} for ${claimant}`,
        `Question: ${ticket.question || ""}`,
      ];
      if (ticket.mode === "hitl") {
        lines.push("HITL: resolve only after the human answers; pass human_input.");
      }
      if (ticket.verify_command) {
        lines.push(`Verify: ${ticket.verify_command}`);
        lines.push(`Run it with the CLI: mythify map verify ${ticket.id}`);
      }
      return lines.join("\n");
    })
  );

  server.registerTool(
    "map_resolve",
    {
      title: "Resolve a claimed decision ticket",
      description:
        "Close a claimed ticket with the decision it reached, then record any fog the answer surfaced. " +
        "A HITL ticket REQUIRES human_input: the agent must not resolve it from its own words. " +
        "A task ticket that stores a verify command requires a passing executed run recorded since the claim. " +
        "Set out_of_scope true instead when the answer shows the ticket sits past the destination. " +
        "Use this once per session, after the ticket's work is actually done.",
      inputSchema: {
        ticket_id: z.string().describe("Ticket id such as T1."),
        answer: z.string().describe("The decision this ticket reached."),
        gist: z.string().optional().describe("One-line index entry. Defaults to the answer."),
        human_input: z
          .string()
          .optional()
          .describe("What the human actually decided. Required for HITL tickets."),
        out_of_scope: z
          .boolean()
          .optional()
          .describe("Rule the ticket past the destination instead of recording a decision."),
        fog: z.array(z.string()).optional().describe("New fog the answer surfaced."),
        scope_out: z.array(z.string()).optional().describe("Work the answer ruled past the destination."),
        map: z.string().optional().describe("Map name; omit to use the active map."),
      },
    },
    guarded((input) => {
      const answer = String(input.answer || "").trim();
      if (!answer) {
        return "[FAIL] Answer required: pass answer describing the decision this ticket resolved.";
      }
      const resolved = resolveMap(input.map);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, record } = resolved;
      const ticket = findTicket(record, input.ticket_id);
      if (!ticket) {
        return `[FAIL] Ticket ${input.ticket_id} not found in map "${slug}".`;
      }
      if (ticket.status !== "open") {
        return `[FAIL] Ticket ${ticketName(ticket)} is already ${ticket.status}.`;
      }
      const outOfScope = input.out_of_scope === true;
      const humanInput = String(input.human_input || "").trim();
      // Ruling a human's question out of scope is itself the human's call, so
      // the HITL gate applies on every resolution path, including out_of_scope.
      let humanInputWaived = false;
      if (ticket.mode === "hitl" && !humanInput) {
        if (requireHumanInputEnabled()) {
          return MAP_HUMAN_INPUT_MESSAGE;
        }
        ticket.human_input_waived = true;
        humanInputWaived = true;
      }
      if (!outOfScope) {
        if (!ticket.claimed_by) {
          return (
            `[FAIL] Ticket ${ticketName(ticket)} is unclaimed. Claim it before resolving ` +
            "so concurrent sessions skip it."
          );
        }
        const blockers = ticketBlockers(record, ticket);
        if (blockers.length > 0) {
          return `[FAIL] Ticket ${ticketName(ticket)} is blocked by: ${blockers.map(ticketName).join(", ")}.`;
        }
        if (ticket.verify_command) {
          const expected = normalizedCommand(ticket.verify_command);
          const cursor = Number.isInteger(ticket.verification_cursor) && ticket.verification_cursor >= 0
            ? ticket.verification_cursor
            : 0;
          const records = readJsonl(verificationsPath()).slice(cursor);
          const evidence = records.find(
            (entry) =>
              entry &&
              entry.kind === "executed" &&
              entry.verified === true &&
              entry.exit_code === 0 &&
              normalizedCommand(entry.command) === expected
          );
          if (!evidence) {
            return (
              `[FAIL] Verified evidence required: ticket ${ticketName(ticket)} stores a verify command, ` +
              "but no passing executed run with exit code 0 matching it was recorded since the ticket was " +
              `claimed. Run the CLI: mythify map verify ${ticket.id}`
            );
          }
          ticket.verified_command = evidence.command || "";
        }
      }
      ticket.human_input = humanInput;
      const now = isoNow();
      ticket.resolution = answer;
      ticket.resolved_at = now;
      ticket.status = outOfScope ? "out_of_scope" : "closed";
      ticket.claimed_by = "";
      if (!Array.isArray(record.out_of_scope)) {
        record.out_of_scope = [];
      }
      if (!Array.isArray(record.decisions)) {
        record.decisions = [];
      }
      if (!Array.isArray(record.fog)) {
        record.fog = [];
      }
      if (outOfScope) {
        record.out_of_scope.push({
          id: nextMapId(record.out_of_scope, "X"),
          note: ticket.title || "",
          reason: answer,
          ticket_id: ticket.id,
          created: now,
        });
      } else {
        record.decisions.push({
          ticket_id: ticket.id,
          title: ticket.title || "",
          gist: input.gist || answer,
          recorded: now,
        });
      }
      for (const note of input.fog || []) {
        record.fog.push({ id: nextMapId(record.fog, "F"), note, graduated_to: "", created: now });
      }
      for (const note of input.scope_out || []) {
        record.out_of_scope.push({
          id: nextMapId(record.out_of_scope, "X"),
          note,
          reason: `ruled past the destination while resolving ${ticket.id}`,
          ticket_id: "",
          created: now,
        });
      }
      if (mapIsClear(record) && record.status === "charting") {
        record.status = "clear";
      }
      saveMap(slug, record);
      const lines = outOfScope
        ? [`[OK] Ruled ticket ${ticketName(ticket)} out of scope in map "${slug}"`, `Why: ${answer}`]
        : [`[OK] Resolved ticket ${ticketName(ticket)} in map "${slug}"`, `Answer: ${answer}`];
      if (ticket.human_input) {
        lines.push(`Human input: ${ticket.human_input}`);
      }
      if (humanInputWaived) {
        lines.push(MAP_HUMAN_INPUT_WAIVED_WARNING);
      }
      lines.push(`Next action: ${mapNextAction(record)}`);
      return lines.join("\n");
    })
  );

  server.registerTool(
    "map_status",
    {
      title: "Show a decision map",
      description:
        "Show the named or active map at low resolution: destination, notes, decisions so far, the frontier, " +
        "claimed and blocked tickets, fog, and out of scope. " +
        "Use this to orient at the start of every map session, before choosing a ticket. It does not mutate state.",
      inputSchema: {
        map: z.string().optional().describe("Map name; omit to use the active map."),
      },
    },
    guarded(({ map: mapName }) => {
      if (
        (mapName === undefined || mapName === null || String(mapName).trim() === "") &&
        !readActiveMapSlug()
      ) {
        return "[OK] No active map yet. Chart one with map_create.";
      }
      const resolved = resolveMap(mapName);
      if (resolved.error) {
        return resolved.error;
      }
      return formatMap(resolved.slug, resolved.record);
    })
  );

  server.registerTool(
    "map_promote",
    {
      title: "Hand a clear map off to a plan",
      description:
        "Create a plan from a map whose way is clear: no open tickets and no fog. The plan carries the destination as " +
        "its goal and the decisions and out-of-scope register as provenance. " +
        "Use this at the edge of the map, when nothing is left to decide and someone should go and do the thing.",
      inputSchema: {
        map: z.string().optional().describe("Map name; omit to use the active map."),
        plan: z.string().optional().describe("Plan name. Defaults to the map name."),
        steps: z
          .array(
            z.object({
              title: z.string().describe("Step title."),
              success_criteria: z.string().optional().describe("How to tell the step is done."),
              verify_command: z.string().optional().describe("Executable proof of the step's done-condition."),
            })
          )
          .optional()
          .describe("Initial plan steps; ids are auto-assigned starting at 1."),
        horizon: z
          .number()
          .int()
          .min(1)
          .optional()
          .describe("Create N default lookahead steps when steps is omitted."),
      },
    },
    guarded(({ map: mapName, plan: planName, steps, horizon }) => {
      const resolved = resolveMap(mapName);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, record } = resolved;
      if (record.status === "promoted") {
        return `[FAIL] Map "${slug}" was already promoted to plan "${record.promoted_plan || ""}".`;
      }
      if (!mapIsClear(record)) {
        const blockers = [];
        if (openTickets(record).length > 0) {
          blockers.push(`${openTickets(record).length} open ticket(s)`);
        }
        if (ungraduatedFog(record).length > 0) {
          blockers.push(`${ungraduatedFog(record).length} fog patch(es)`);
        }
        return (
          `[FAIL] Map "${slug}" is not clear yet: ${blockers.join(" and ")}. ` +
          "A map is done when nothing is left to decide."
        );
      }
      let planSteps = steps || [];
      if (steps === undefined && horizon !== undefined) {
        planSteps = buildDefaultPlanSteps(horizon);
      }
      const planSlug = createPlanRecord({
        goal: record.destination || "",
        name: planName || slug,
        steps: planSteps,
        source: {
          kind: "map",
          map: slug,
          destination: record.destination || "",
          decisions: record.decisions || [],
          out_of_scope: record.out_of_scope || [],
        },
      });
      record.status = "promoted";
      record.promoted_plan = planSlug;
      saveMap(slug, record);
      clearActiveMapSlug(slug);
      const lines = [
        `[OK] Promoted map "${slug}" to plan "${planSlug}"`,
        `Destination: ${record.destination || ""}`,
        `Carried into the plan: ${(record.decisions || []).length} decision(s), ` +
          `${(record.out_of_scope || []).length} out-of-scope entr(ies).`,
      ];
      if (planSteps.length === 0) {
        lines.push("The plan has no steps yet; add them with plan_add_step.");
      }
      return lines.join("\n");
    })
  );
}
