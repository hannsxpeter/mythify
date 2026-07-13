import { spawnSync } from "node:child_process";

export function terminateProcessTree(
  child,
  { platform = process.platform, runTaskkill = spawnSync } = {}
) {
  if (!child || !child.pid) {
    return true;
  }
  if (platform === "win32") {
    const result = runTaskkill("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
    if (!result.error && result.status === 0) {
      return true;
    }
  } else {
    try {
      process.kill(-child.pid, "SIGKILL");
      return true;
    } catch {
      // Fall through to killing only the parent and report failed containment.
    }
  }
  try {
    child.kill("SIGKILL");
  } catch {
    // The parent already exited, but descendant containment is still unknown.
  }
  return false;
}
