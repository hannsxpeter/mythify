// Read-only parsers for godplans (.godplans/PLAN.mdx) and godaudits
// (.godaudits/AUDIT.mdx) artifacts. Byte-for-byte behavioral mirror of
// scripts/mythify_godfiles.py; keep the two in lockstep (tests/test_interop.py
// asserts parity on shared fixtures). Mythify only reads these artifacts:
// checkbox flips stay with the executing agent per the artifacts' own rules.

import fs from "node:fs";
import path from "node:path";

export const GODPLANS_DIR_NAME = ".godplans";
export const GODAUDITS_DIR_NAME = ".godaudits";
export const GODPLANS_FILENAMES = ["PLAN.mdx", "PLAN.md"];
export const GODAUDITS_FILENAMES = ["AUDIT.mdx", "AUDIT.md"];

const TASK_RE = /^(~~)?- \[( |x|X)\]\s+((?:GP|GA)-\S+)\s*(.*?)(?:~~)?\s*$/;
const FLAG_RE = /^\[([^\]]+)\]\s*/;
const WAVE_FLAG_RE = /^W[0-9][0-9.]*$/;
const FIELD_RE = /^(?:\t|\s{2,})- ([A-Za-z][A-Za-z ]*?):\s*(.*)$/;
const NOTE_RE = /^(?:\t|\s{2,})- Note \(/;
const PHASE_RE = /^## Phase\s+(\S+):\s*(.*)$/;
const WAVE_RE = /^### Wave\s+(\S+)/;
const FINDING_RE = /^#### (F-[A-Z]+-[0-9]+)\s+(.*?)\s*\[([^\]]+)\]\s*$/;
const INT_RE = /^-?[0-9]+$/;
// CommonMark fence: 3+ backticks or 3+ tildes, same run closes the fence.
const FENCE_RE = /^(`{3,}|~{3,})/;
// Python str.splitlines() terminator set, so both parsers agree on where lines
// break (U+2028/U+2029/NEL/form-feed/lone-CR included), never seeing an
// embedded terminator inside a "line".
const LINE_SPLIT_RE = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;


// Null-prototype map so a field name like "constructor" cannot resolve to an
// inherited Object property.
const FIELD_KEYS = Object.assign(Object.create(null), {
  "files": "files",
  "depends on": "depends_on",
  "reuses": "reuses",
  "acceptance": "acceptance",
  "verify": "verify_command",
  "requirements": "requirements",
  "fixes": "fixes",
  "checks": "checks",
});
const LIST_FIELDS = ["files", "depends_on", "fixes"];

function stripBom(text) {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
}

function findArtifact(directory, filenames) {
  for (const name of filenames) {
    const candidate = path.join(directory, name);
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      return candidate;
    }
  }
  return null;
}

export function findGodplansFile(root) {
  return findArtifact(path.join(root, GODPLANS_DIR_NAME), GODPLANS_FILENAMES);
}

export function findGodauditsFile(root) {
  return findArtifact(path.join(root, GODAUDITS_DIR_NAME), GODAUDITS_FILENAMES);
}

function scalar(value) {
  const text = value.trim();
  if (
    text.length >= 2 &&
    (text.startsWith('"') || text.startsWith("'")) &&
    text.endsWith(text[0])
  ) {
    return text.slice(1, -1);
  }
  if (text === "true" || text === "True") {
    return true;
  }
  if (text === "false" || text === "False") {
    return false;
  }
  if (INT_RE.test(text)) {
    return parseInt(text, 10);
  }
  return text;
}

export function parseGodFrontmatter(text) {
  const lines = stripBom(text).split(LINE_SPLIT_RE);
  if (lines.length === 0 || lines[0].trim() !== "---") {
    return {};
  }
  const data = {};
  let current = null;
  for (const line of lines.slice(1)) {
    if (line.trim() === "---") {
      break;
    }
    if (line.trim() === "" || line.trimStart().startsWith("#")) {
      continue;
    }
    const indent = line.length - line.trimStart().length;
    const stripped = line.trim();
    if (stripped.startsWith("- ")) {
      continue;
    }
    const colon = stripped.indexOf(":");
    if (colon === -1) {
      continue;
    }
    const key = stripped.slice(0, colon).trim();
    const value = stripped.slice(colon + 1).trim();
    if (indent === 0) {
      if (value === "") {
        data[key] = {};
        current = key;
      } else {
        data[key] = scalar(value);
        current = null;
      }
    } else if (
      current !== null &&
      typeof data[current] === "object" &&
      data[current] !== null &&
      !Array.isArray(data[current])
    ) {
      if (value !== "") {
        data[current][key] = scalar(value);
      }
    }
  }
  return data;
}

function stripBackticks(value) {
  const text = value.trim();
  if (text.startsWith("`") && text.endsWith("`") && text.length >= 2) {
    return text.slice(1, -1).trim();
  }
  return text;
}

function splitList(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item !== "");
}

function finishTask(task, tasks) {
  if (task === null) {
    return;
  }
  for (const key of LIST_FIELDS) {
    if (key in task && typeof task[key] === "string") {
      let items = splitList(task[key]);
      if (items.length === 1 && items[0].toLowerCase().startsWith("none")) {
        items = [];
      }
      task[key] = items;
    }
  }
  const verify = task.verify_command || "";
  task.verify_command = verify ? stripBackticks(verify) : "";
  tasks.push(task);
}

export function parseGodDocument(text) {
  const frontmatter = parseGodFrontmatter(text);
  const tasks = [];
  const findings = [];
  const phases = [];
  let currentPhase = null;
  let currentWave = "";
  let task = null;
  let field = null;
  let finding = null;
  let fence = null;
  for (const line of stripBom(text).split(LINE_SPLIT_RE)) {
    const fenceMatch = line.trimStart().match(FENCE_RE);
    if (fence === null) {
      if (fenceMatch) {
        const marker = fenceMatch[1];
        fence = { char: marker[0], length: marker.length };
        continue;
      }
    } else {
      if (fenceMatch) {
        const marker = fenceMatch[1];
        const closes =
          marker[0] === fence.char &&
          marker.length >= fence.length &&
          line.trim() === marker;
        if (closes) {
          fence = null;
        }
      }
      continue;
    }
    if (line.startsWith("#")) {
      finishTask(task, tasks);
      task = null;
      field = null;
      finding = null;
      const phaseMatch = line.match(PHASE_RE);
      if (phaseMatch) {
        currentPhase = { number: phaseMatch[1], title: phaseMatch[2].trim() };
        phases.push(currentPhase);
        currentWave = "";
        continue;
      }
      const waveMatch = line.match(WAVE_RE);
      if (waveMatch) {
        currentWave = waveMatch[1].replace(/\.+$/, "");
        continue;
      }
      const findingMatch = line.match(FINDING_RE);
      if (findingMatch) {
        const triple = findingMatch[3].split("|").map((part) => part.trim());
        finding = {
          id: findingMatch[1],
          title: findingMatch[2].trim(),
          severity: triple[0] || "",
          confidence: triple[1] || "",
          effort: triple[2] || "",
          status: "open",
          remediation: "",
        };
        findings.push(finding);
      }
      continue;
    }
    const taskMatch = line.match(TASK_RE);
    if (taskMatch) {
      finishTask(task, tasks);
      field = null;
      finding = null;
      let rest = taskMatch[4];
      let parallel = false;
      let wave = currentWave;
      for (;;) {
        const flagMatch = rest.match(FLAG_RE);
        if (!flagMatch) {
          break;
        }
        const flag = flagMatch[1];
        if (flag === "P") {
          parallel = true;
        } else if (WAVE_FLAG_RE.test(flag)) {
          wave = flag.slice(1);
        } else {
          break;
        }
        rest = rest.slice(flagMatch[0].length);
      }
      task = {
        id: taskMatch[3],
        title: rest.trim(),
        checked: taskMatch[2] === "x" || taskMatch[2] === "X",
        superseded: Boolean(taskMatch[1]),
        parallel,
        wave,
        phase_number: currentPhase ? currentPhase.number : "",
        phase_title: currentPhase ? currentPhase.title : "",
        notes: [],
      };
      continue;
    }
    if (line !== "" && !/^\s/.test(line)) {
      if (finding !== null && line.startsWith("- ")) {
        const colon = line.slice(2).indexOf(":");
        if (colon !== -1) {
          const key = line.slice(2, 2 + colon).trim().toLowerCase();
          const value = line.slice(2 + colon + 1).trim();
          if (key === "status") {
            finding.status = value;
          } else if (key === "remediation") {
            finding.remediation = value;
          }
        }
        continue;
      }
      finishTask(task, tasks);
      task = null;
      field = null;
      finding = null;
      continue;
    }
    if (task !== null) {
      if (NOTE_RE.test(line)) {
        task.notes.push(line.trim().slice(2));
        field = null;
        continue;
      }
      const fieldMatch = line.match(FIELD_RE);
      if (fieldMatch) {
        const key = FIELD_KEYS[fieldMatch[1].trim().toLowerCase()];
        if (key) {
          task[key] = fieldMatch[2].trim();
          field = key;
        } else {
          field = null;
        }
        continue;
      }
      if (field && line.trim() !== "") {
        task[field] = `${task[field]} ${line.trim()}`.trim();
      }
      continue;
    }
  }
  finishTask(task, tasks);
  const liveTasks = tasks.filter((entry) => !entry.superseded);
  const done = liveTasks.filter((entry) => entry.checked).length;
  const nextTask = liveTasks.find((entry) => !entry.checked) || null;
  return {
    frontmatter,
    tasks,
    findings,
    phases,
    counts: {
      tasks_total: liveTasks.length,
      tasks_done: done,
      tasks_open: liveTasks.length - done,
    },
    next_task: nextTask,
  };
}

function digestCounterDrift(frontmatter, counts) {
  let progress = frontmatter.progress;
  if (typeof progress !== "object" || progress === null || Array.isArray(progress)) {
    progress = frontmatter.counts;
  }
  if (typeof progress !== "object" || progress === null || Array.isArray(progress)) {
    return false;
  }
  let drift = false;
  for (const key of ["tasks_total", "tasks_done"]) {
    const digestValue = progress[key];
    if (Number.isInteger(digestValue) && digestValue !== counts[key]) {
      drift = true;
    }
  }
  return drift;
}

export function loadGodArtifact(artifactPath, kind) {
  // Load failures set load_error so callers branch on that structural flag,
  // never on the author-controlled status string (which could itself be the
  // word "unreadable" or "unrecognized").
  let text;
  try {
    text = fs.readFileSync(artifactPath, "utf8");
  } catch (error) {
    return {
      kind,
      path: String(artifactPath),
      status: "unreadable",
      load_error: true,
      detail: error.message,
    };
  }
  const parsed = parseGodDocument(text);
  const frontmatter = parsed.frontmatter;
  const counts = parsed.counts;
  const expectedPrefix = kind === "godplans" ? "GP-" : "GA-";
  const recognized = parsed.tasks.some((entry) => entry.id.startsWith(expectedPrefix));
  if (!recognized && Object.keys(frontmatter).length === 0) {
    return {
      kind,
      path: String(artifactPath),
      status: "unrecognized",
      load_error: true,
      detail: `no frontmatter and no ${expectedPrefix} tasks found`,
    };
  }
  const digest = {
    kind,
    path: String(artifactPath),
    status: String(frontmatter.status || "unknown"),
    name: String(frontmatter.name || ""),
    counts,
    counter_drift: digestCounterDrift(frontmatter, counts),
    next_task: parsed.next_task,
    tasks: parsed.tasks,
    phases: parsed.phases,
  };
  if (kind === "godplans") {
    digest.plan_version = frontmatter.plan_version ?? null;
  } else {
    digest.audit_version = frontmatter.audit_version ?? null;
    digest.plan_aware = Boolean(frontmatter.plan_aware);
    const scores = frontmatter.scores;
    const hasScores = typeof scores === "object" && scores !== null && !Array.isArray(scores);
    digest.overall_score = hasScores ? scores.overall ?? null : null;
    digest.verdict = hasScores ? String(scores.verdict || "") : "";
    digest.findings = parsed.findings;
    digest.open_critical = parsed.findings.filter(
      (finding) => finding.status === "open" && finding.severity === "Critical"
    ).length;
    digest.open_high = parsed.findings.filter(
      (finding) => finding.status === "open" && finding.severity === "High"
    ).length;
  }
  return digest;
}

function taskProgressDetail(digest) {
  const counts = digest.counts || {};
  const parts = [`${counts.tasks_done || 0}/${counts.tasks_total || 0} tasks done`];
  const nextTask = digest.next_task;
  if (nextTask) {
    parts.push(`next ${nextTask.id} ${nextTask.title}`);
  }
  if (digest.counter_drift) {
    parts.push("frontmatter counters disagree with checkboxes");
  }
  return parts.join("; ");
}

export function godplansSummary(root) {
  // Callers surface the artifact when present is true and treat the file as
  // absent otherwise; they never key off the author-controlled status.
  const artifactPath = findGodplansFile(root);
  if (artifactPath === null) {
    return { status: "missing", present: false, path: "", detail: "no .godplans plan found" };
  }
  const digest = loadGodArtifact(artifactPath, "godplans");
  if (digest.load_error) {
    return { status: digest.status, present: true, path: digest.path, detail: digest.detail || "" };
  }
  const summary = {
    status: digest.status,
    present: true,
    path: digest.path,
    detail: taskProgressDetail(digest),
    tasks_total: digest.counts.tasks_total,
    tasks_done: digest.counts.tasks_done,
    counter_drift: digest.counter_drift,
  };
  if (digest.next_task) {
    summary.next_task_id = digest.next_task.id;
    summary.next_task_title = digest.next_task.title;
  }
  return summary;
}

export function godauditsSummary(root) {
  // Callers surface the artifact when present is true and treat the file as
  // absent otherwise; they never key off the author-controlled status.
  const artifactPath = findGodauditsFile(root);
  if (artifactPath === null) {
    return { status: "missing", present: false, path: "", detail: "no .godaudits audit found" };
  }
  const digest = loadGodArtifact(artifactPath, "godaudits");
  if (digest.load_error) {
    return { status: digest.status, present: true, path: digest.path, detail: digest.detail || "" };
  }
  const detailParts = [];
  if (digest.overall_score !== null && digest.overall_score !== undefined) {
    const verdict = digest.verdict || "unrated";
    detailParts.push(`score ${digest.overall_score} (${verdict})`);
  }
  if (digest.open_critical) {
    detailParts.push(`${digest.open_critical} open Critical`);
  }
  if (digest.open_high) {
    detailParts.push(`${digest.open_high} open High`);
  }
  detailParts.push(taskProgressDetail(digest));
  const summary = {
    status: digest.status,
    present: true,
    path: digest.path,
    detail: detailParts.join("; "),
    tasks_total: digest.counts.tasks_total,
    tasks_done: digest.counts.tasks_done,
    counter_drift: digest.counter_drift,
    open_critical: digest.open_critical || 0,
    open_high: digest.open_high || 0,
    overall_score: digest.overall_score ?? null,
    verdict: digest.verdict || "",
  };
  if (digest.next_task) {
    summary.next_task_id = digest.next_task.id;
    summary.next_task_title = digest.next_task.title;
  }
  return summary;
}

export function godArtifactHasOpenTasks(view) {
  if (!view) {
    return false;
  }
  const total = view.tasks_total;
  const done = view.tasks_done;
  if (!Number.isInteger(total) || !Number.isInteger(done)) {
    return false;
  }
  return done < total;
}
