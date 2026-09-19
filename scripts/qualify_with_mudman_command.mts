/**
 * Run Mudman Command's REAL verifier against StreakFit on localhost.
 *
 * Deliberately outside Command's repository and writing nothing into it:
 * StreakFit is not registered in VERIFIED_APPS and registering it is a change
 * to another project, which needs Tim. This constructs the same TargetApp shape
 * its runner takes and calls the runner itself, so the result is Command's
 * judgement rather than a local imitation of it.
 */
import { verifyApp } from "/home/mudmantim/projects/mudman-command/src/lib/verification/runner.ts";

const run = await verifyApp({
  id: "streakfit",
  label: "StreakFit",
  baseUrl: process.env.STREAKFIT_BASE_URL ?? "http://localhost:5000",
});

console.log(`\n${run.application} — ${run.status} (${run.level})`);
console.log(`recommendation: ${run.recommendation}   cost: ${run.costIncurred}`);
if (run.build) {
  console.log(`build: ${run.build.application} ${run.build.version} ${run.build.gitSha?.slice(0, 7)} ` +
              `· migrations ${run.build.migration.appliedCount} @ ${run.build.migration.latest}`);
} else {
  console.log(`build identity unavailable: ${run.buildError}`);
}
console.log("");
for (const c of run.checks) {
  const flag = c.critical ? "!" : " ";
  console.log(`${flag} ${c.status.padEnd(7)} ${c.level.padEnd(8)} ${c.id.padEnd(22)} ${c.observed}`);
  if (c.failureReason) console.log(`               why: ${c.failureReason}`);
}
