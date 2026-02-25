"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, CheckCircle2, Mail, Play, RefreshCcw, ShieldCheck, XCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

type ApiOutcome = {
  status: number;
  data: unknown;
};

type OrchestrationStatus = {
  instance_id?: string;
  runtime_status?: string;
  custom_status?: unknown;
  output?: unknown;
};

const defaultResources = JSON.stringify(
  [
    {
      resource_id:
        "/subscriptions/<sub-id>/resourceGroups/demo-rg/providers/Microsoft.Compute/virtualMachines/vm-cost-demo",
      monthly_cost: 380,
      metrics: { cpuUtilizationPercent: 3.4 },
    },
  ],
  null,
  2,
);

function buildLogicPayload() {
  const approvalId = `ui-${Date.now()}`;
  return JSON.stringify(
    {
      approvalId,
      batchId: `batch-${approvalId}`,
      stage: "finance",
      deadline: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
      riskSummary: {
        level: "low",
        score: 0.1,
        reasons: ["UI test run"],
      },
    },
    null,
    2,
  );
}

function toPrettyJson(value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

async function requestJson(url: string, init?: RequestInit): Promise<ApiOutcome> {
  const response = await fetch(url, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const raw = await response.text();
  let data: unknown = raw;
  try {
    data = raw ? (JSON.parse(raw) as unknown) : null;
  } catch {
    data = raw;
  }

  if (!response.ok) {
    const message =
      typeof data === "object" && data !== null && "error" in data
        ? String((data as { error?: string }).error)
        : `Request failed (${response.status})`;
    throw new Error(message);
  }

  return { status: response.status, data };
}

export default function Home() {
  const [resourcesText, setResourcesText] = useState(defaultResources);
  const [runId, setRunId] = useState("");
  const [instanceId, setInstanceId] = useState("");
  const [healthResult, setHealthResult] = useState<unknown>(null);
  const [statusResult, setStatusResult] = useState<OrchestrationStatus | null>(null);
  const [decisionStage, setDecisionStage] = useState<"finance" | "engineering">("finance");
  const [approvalId, setApprovalId] = useState("");
  const [decisionNotes, setDecisionNotes] = useState("");
  const [logicPayload, setLogicPayload] = useState(buildLogicPayload());
  const [logicResult, setLogicResult] = useState<unknown>(null);
  const [message, setMessage] = useState("Ready.");
  const [errorMessage, setErrorMessage] = useState("");
  const [isWorking, setIsWorking] = useState<string | null>(null);
  const [autoPoll, setAutoPoll] = useState(true);

  const runtimeStatus = statusResult?.runtime_status ?? "not started";
  const approvalDestination = process.env.NEXT_PUBLIC_APPROVAL_RECIPIENT ?? "justinjoy@microsoft.com";

  const refreshStatus = useCallback(
    async (targetId?: string) => {
      const id = targetId ?? instanceId;
      if (!id) {
        throw new Error("Provide an instance ID first.");
      }
      const outcome = await requestJson(`/api/agent/status/${encodeURIComponent(id)}`);
      setStatusResult((outcome.data as OrchestrationStatus) ?? null);
      setMessage(`Status updated for ${id}.`);
    },
    [instanceId],
  );

  useEffect(() => {
    if (!autoPoll || !instanceId) {
      return;
    }

    const timer = setInterval(() => {
      void refreshStatus(instanceId).catch(() => undefined);
    }, 5000);

    return () => clearInterval(timer);
  }, [autoPoll, instanceId, refreshStatus]);

  const runtimeBadgeVariant = useMemo(() => {
    const value = runtimeStatus.toLowerCase();
    if (value.includes("completed")) {
      return "default";
    }
    if (value.includes("failed") || value.includes("terminated")) {
      return "destructive";
    }
    return "secondary";
  }, [runtimeStatus]);

  async function withUiGuard(name: string, action: () => Promise<void>) {
    setIsWorking(name);
    setErrorMessage("");
    try {
      await action();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Unexpected error.");
      setMessage("Action failed.");
    } finally {
      setIsWorking(null);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-6 px-5 py-8 md:px-8">
      <header className="noise-overlay panel-glass relative overflow-hidden rounded-3xl border px-6 py-6 shadow-2xl shadow-cyan-950/25">
        <div className="absolute -top-14 -right-10 h-52 w-52 rounded-full bg-cyan-300/20 blur-3xl" />
        <div className="absolute -bottom-20 -left-14 h-52 w-52 rounded-full bg-fuchsia-400/20 blur-3xl" />
        <div className="relative flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div className="space-y-2">
            <p className="text-xs uppercase tracking-[0.22em] text-cyan-200/80">Azure Cost Optimiser</p>
            <h1 className="font-display text-3xl leading-tight font-semibold text-white md:text-4xl">
              Command Deck for End-to-End Agent Validation
            </h1>
            <p className="max-w-2xl text-sm text-slate-200/85">
              Trigger orchestration runs, monitor durable state, submit approvals, and fire Logic App email tests from one
              place.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge className="bg-cyan-300/20 text-cyan-100 border-cyan-300/30">
              <ShieldCheck className="mr-1 size-3.5" />
              Agent APIs proxied server-side
            </Badge>
            <Badge className="bg-fuchsia-300/20 text-fuchsia-100 border-fuchsia-300/30">
              <Mail className="mr-1 size-3.5" />
              Recipient: {approvalDestination}
            </Badge>
          </div>
        </div>
      </header>

      {errorMessage ? (
        <Alert variant="destructive" className="panel-glass border-destructive/60">
          <XCircle />
          <AlertTitle>Action error</AlertTitle>
          <AlertDescription>{errorMessage}</AlertDescription>
        </Alert>
      ) : (
        <Alert className="panel-glass">
          <Activity />
          <AlertTitle>Runtime message</AlertTitle>
          <AlertDescription>{message}</AlertDescription>
        </Alert>
      )}

      <Tabs defaultValue="agent" className="gap-4">
        <TabsList variant="line" className="bg-transparent p-0">
          <TabsTrigger value="agent">Agent Orchestration</TabsTrigger>
          <TabsTrigger value="logicapp">Logic App Email Flow</TabsTrigger>
        </TabsList>

        <TabsContent value="agent">
          <div className="grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">
            <Card className="panel-glass noise-overlay">
              <CardHeader>
                <CardTitle className="font-display text-xl">Start a cost optimisation run</CardTitle>
                <CardDescription>
                  Provide a resources array and optionally set a custom run ID for predictable re-tests.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <Input
                  placeholder="Optional run ID (e.g. ui-regression-001)"
                  value={runId}
                  onChange={(event) => setRunId(event.target.value)}
                />
                <Textarea
                  className="min-h-44 font-mono text-xs"
                  value={resourcesText}
                  onChange={(event) => setResourcesText(event.target.value)}
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={() =>
                      withUiGuard("start", async () => {
                        const parsed = JSON.parse(resourcesText) as unknown;
                        if (!Array.isArray(parsed)) {
                          throw new Error("resources payload must be a JSON array.");
                        }
                        const payload = {
                          user_id: "dashboard-user",
                          resources: parsed,
                          ...(runId.trim() ? { run_id: runId.trim() } : {}),
                        };
                        const outcome = await requestJson("/api/agent/report", {
                          method: "POST",
                          body: JSON.stringify(payload),
                        });
                        const data = outcome.data as { instance_id?: string };
                        if (data.instance_id) {
                          setInstanceId(data.instance_id);
                        }
                        setStatusResult(null);
                        setMessage(`Run started (${data.instance_id ?? "no instance returned"}).`);
                      })
                    }
                    disabled={isWorking !== null}
                  >
                    <Play className="size-4" />
                    Start run
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() =>
                      withUiGuard("health", async () => {
                        const outcome = await requestJson("/api/agent/health");
                        setHealthResult(outcome.data);
                        setMessage("Health endpoint returned successfully.");
                      })
                    }
                    disabled={isWorking !== null}
                  >
                    <ShieldCheck className="size-4" />
                    Check health
                  </Button>
                </div>
                <Separator />
                <div className="space-y-2">
                  <p className="text-xs uppercase tracking-[0.2em] text-slate-300/80">Health payload</p>
                  <pre className="max-h-52 overflow-auto rounded-lg border bg-black/25 p-3 text-xs text-slate-100">
                    {toPrettyJson(healthResult) || "No health call yet."}
                  </pre>
                </div>
              </CardContent>
            </Card>

            <Card className="panel-glass noise-overlay">
              <CardHeader>
                <CardTitle className="font-display text-xl">Status + approval controls</CardTitle>
                <CardDescription>Track durable runtime and issue approval decisions when required.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <Input
                  placeholder="Instance ID"
                  value={instanceId}
                  onChange={(event) => setInstanceId(event.target.value)}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="secondary"
                    onClick={() => withUiGuard("status", async () => refreshStatus())}
                    disabled={isWorking !== null}
                  >
                    <RefreshCcw className="size-4" />
                    Refresh status
                  </Button>
                  <Button
                    variant={autoPoll ? "default" : "outline"}
                    onClick={() => setAutoPoll((value) => !value)}
                    disabled={!instanceId}
                  >
                    Auto poll: {autoPoll ? "On" : "Off"}
                  </Button>
                  <Badge variant={runtimeBadgeVariant}>{runtimeStatus}</Badge>
                </div>
                <div className="space-y-2">
                  <p className="text-xs uppercase tracking-[0.2em] text-slate-300/80">Runtime payload</p>
                  <pre className="max-h-52 overflow-auto rounded-lg border bg-black/25 p-3 text-xs text-slate-100">
                    {toPrettyJson(statusResult) || "No status fetched yet."}
                  </pre>
                </div>
                <Separator />
                <div className="grid gap-3 md:grid-cols-2">
                  <Button
                    variant={decisionStage === "finance" ? "default" : "outline"}
                    onClick={() => setDecisionStage("finance")}
                  >
                    Stage: Finance
                  </Button>
                  <Button
                    variant={decisionStage === "engineering" ? "default" : "outline"}
                    onClick={() => setDecisionStage("engineering")}
                  >
                    Stage: Engineering
                  </Button>
                </div>
                <Input
                  placeholder="Approval ID (optional)"
                  value={approvalId}
                  onChange={(event) => setApprovalId(event.target.value)}
                />
                <Textarea
                  className="min-h-24"
                  placeholder="Decision notes (optional)"
                  value={decisionNotes}
                  onChange={(event) => setDecisionNotes(event.target.value)}
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={() =>
                      withUiGuard("approve", async () => {
                        if (!instanceId.trim()) {
                          throw new Error("Instance ID is required before sending approval.");
                        }
                        await requestJson(`/api/agent/decide/${encodeURIComponent(instanceId.trim())}`, {
                          method: "POST",
                          body: JSON.stringify({
                            decision: "approve",
                            stage: decisionStage,
                            approvalId: approvalId.trim() || undefined,
                            notes: decisionNotes.trim() || undefined,
                          }),
                        });
                        setMessage("Approval sent.");
                        await refreshStatus(instanceId.trim());
                      })
                    }
                    disabled={isWorking !== null}
                  >
                    <CheckCircle2 className="size-4" />
                    Approve
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() =>
                      withUiGuard("reject", async () => {
                        if (!instanceId.trim()) {
                          throw new Error("Instance ID is required before sending rejection.");
                        }
                        await requestJson(`/api/agent/decide/${encodeURIComponent(instanceId.trim())}`, {
                          method: "POST",
                          body: JSON.stringify({
                            decision: "reject",
                            stage: decisionStage,
                            approvalId: approvalId.trim() || undefined,
                            notes: decisionNotes.trim() || undefined,
                          }),
                        });
                        setMessage("Rejection sent.");
                        await refreshStatus(instanceId.trim());
                      })
                    }
                    disabled={isWorking !== null}
                  >
                    <XCircle className="size-4" />
                    Reject
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="logicapp">
          <Card className="panel-glass noise-overlay">
            <CardHeader>
              <CardTitle className="font-display text-xl">Trigger approval email workflow directly</CardTitle>
              <CardDescription>
                Sends a request payload to the Logic App HTTP trigger so you can validate Outlook delivery and callback
                behavior.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Textarea
                className="min-h-44 font-mono text-xs"
                value={logicPayload}
                onChange={(event) => setLogicPayload(event.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() =>
                    withUiGuard("logic", async () => {
                      const parsed = JSON.parse(logicPayload) as unknown;
                      if (!parsed || typeof parsed !== "object") {
                        throw new Error("Logic App payload must be a JSON object.");
                      }
                      const outcome = await requestJson("/api/logicapp/trigger", {
                        method: "POST",
                        body: JSON.stringify(parsed),
                      });
                      setLogicResult({ status: outcome.status, body: outcome.data });
                      setMessage("Logic App trigger invoked.");
                    })
                  }
                  disabled={isWorking !== null}
                >
                  <Mail className="size-4" />
                  Trigger Logic App
                </Button>
                <Button
                  variant="outline"
                  onClick={() => setLogicPayload(buildLogicPayload())}
                  disabled={isWorking !== null}
                >
                  Regenerate payload
                </Button>
              </div>
              <Alert className="border-cyan-300/30 bg-cyan-300/10 text-cyan-50">
                <Mail />
                <AlertTitle>Approval destination</AlertTitle>
                <AlertDescription>
                  The Logic App finance and engineering emails are currently configured to{" "}
                  <span className="font-semibold">{approvalDestination}</span>.
                </AlertDescription>
              </Alert>
              <pre className="max-h-64 overflow-auto rounded-lg border bg-black/25 p-3 text-xs text-slate-100">
                {toPrettyJson(logicResult) || "No Logic App trigger call yet."}
              </pre>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </main>
  );
}
