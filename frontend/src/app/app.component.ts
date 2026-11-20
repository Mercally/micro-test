import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LabSettings, OutboxEvent, SaleView, SalesApiService, StepAttempt, SystemView } from './sales-api.service';

interface TreeGeneration {
  event: OutboxEvent;
  attempts: StepAttempt[];
}

interface Scenario {
  step: '' | 'document' | 'cm' | 'insurer';
  kind: 'transient' | 'permanent';
  times: number;
}

interface Preset extends Scenario {
  name: string;
}

/** Must match 04-messaging: 1 + MaxAttempts(2) per step, MaxReceiveCount 3, ReleaseDelaySeconds 30. */
const STEP_ATTEMPTS = 3;
const DELIVERIES = 3;
const RELEASE_DELAY_S = 30;
const STEP_LABELS: Record<string, string> = {
  document: '1 · Documento firmado → S3 temporal',
  cm: '2 · Content Manager',
  insurer: '3 · Aseguradora',
  cleanup: 'Limpieza del archivo temporal',
  fail: 'Marcar venta fallida'
};

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <main class="page-shell">
      <header class="topbar">
        <div>
          <p class="eyebrow">LABORATORIO AWS</p>
          <h1>Panel de pruebas</h1>
          <p class="subtitle">Configura escenarios de fallo y carga, y observa quién reacciona: colas, Step Functions, KEDA y Karpenter.</p>
        </div>
        <span class="live-badge" [class.off]="!autoRefresh"><span></span>{{ autoRefresh ? 'En vivo · 3 s' : 'Pausado' }}</span>
      </header>

      <!-- ===== Sistema en vivo ===== -->
      <section class="metrics" *ngIf="system as s; else systemPending">
        <article class="metric">
          <p class="eyebrow">COLA PRINCIPAL</p>
          <ng-container *ngIf="s.queues.main.error; else mainOk"><small class="row-error">{{ s.queues.main.error }}</small></ng-container>
          <ng-template #mainOk>
            <strong>{{ s.queues.main.visible }}</strong><span class="unit">visibles</span>
            <p class="muted">{{ s.queues.main.inFlight }} en proceso · {{ s.queues.main.delayed }} retrasados</p>
          </ng-template>
        </article>
        <article class="metric" [class.alert]="(s.queues.dlq.visible ?? 0) > 0">
          <p class="eyebrow">DLQ</p>
          <ng-container *ngIf="s.queues.dlq.error; else dlqOk"><small class="row-error">{{ s.queues.dlq.error }}</small></ng-container>
          <ng-template #dlqOk>
            <strong>{{ s.queues.dlq.visible }}</strong><span class="unit">mensajes</span>
            <p class="muted">{{ s.queues.dlq.name }}</p>
            <button class="check-button" type="button" (click)="redrive()" [disabled]="!s.queues.dlq.visible || busy">Redrive → cola principal</button>
          </ng-template>
        </article>
        <article class="metric">
          <p class="eyebrow">OUTBOX · EN CURSO</p>
          <strong>{{ s.outboxPending }}</strong><span class="unit">por publicar</span>
          <p class="muted">{{ s.running }} pasos ejecutándose ahora</p>
        </article>
        <article class="metric">
          <p class="eyebrow">PODS (KEDA)</p>
          <ng-container *ngIf="s.kubernetes.available; else noK8s">
            <p class="pod-line" *ngFor="let d of s.kubernetes.deployments">
              <span class="node-title">{{ d.name }}</span> {{ d.ready }}/{{ d.desired }}
              <span class="status processing" *ngIf="s.kubernetes.pendingPods?.[d.name]">{{ s.kubernetes.pendingPods?.[d.name] }} pending</span>
            </p>
            <p class="muted" *ngFor="let h of s.kubernetes.hpa">HPA {{ h.name }}: {{ h.current }} → {{ h.desired }} (máx {{ h.max }})</p>
          </ng-container>
        </article>
        <article class="metric">
          <p class="eyebrow">NODOS (KARPENTER)</p>
          <ng-container *ngIf="s.kubernetes.available; else noK8s">
            <strong>{{ s.kubernetes.nodes?.length }}</strong><span class="unit">nodos</span>
            <p class="muted" *ngFor="let g of nodeGroups(s)">{{ g }}</p>
          </ng-container>
        </article>
        <article class="metric">
          <p class="eyebrow">VENTAS</p>
          <p class="totals"><span *ngFor="let e of entries(s.sales)" class="status" [ngClass]="statusClass(e.key)">{{ e.key }} {{ e.value }}</span></p>
        </article>
        <article class="metric wide">
          <p class="eyebrow">LÍMITES (PRESUPUESTO)</p>
          <p class="muted">Ejecuciones simultáneas (Lambda/Step Functions): <b>{{ s.limits['maxConcurrency'] }}</b> ·
            pods api máx: <b>{{ s.limits['apiMaxReplicas'] }}</b> ({{ s.limits['messagesPerPod'] }} msg/pod) ·
            vCPU Karpenter máx: <b>{{ s.limits['karpenterCpuLimit'] }}</b> · CPU reservada por pod api: <b>{{ s.limits['apiCpuRequest'] }}</b> ·
            intentos por paso: {{ s.limits['stepAttempts'] }} · entregas SQS: {{ s.limits['deliveries'] }}</p>
          <p class="muted">Se cambian por CLI (stack 04 <code>MaxConcurrency</code>; <code>bootstrap.sh</code> para pods y vCPU). Ver CARGA.md y ESCALADO.md.</p>
        </article>
        <ng-template #noK8s><p class="muted">{{ s.kubernetes.error || 'Sin acceso a Kubernetes' }}</p></ng-template>
      </section>
      <ng-template #systemPending><section class="metrics"><article class="metric wide"><p class="muted">{{ systemError || 'Consultando sistema…' }}</p></article></section></ng-template>

      <!-- ===== 01 Escenario (abierto por defecto) ===== -->
      <details class="card panel" open>
        <summary class="section-heading">
          <div><p class="eyebrow">ESCENARIO</p><h2>Fallos y carga</h2></div>
          <span class="step-number">01</span>
        </summary>
          <div class="presets">
            <button *ngFor="let p of presets" type="button" class="chip" [class.active]="isPreset(p)" (click)="apply(p)">{{ p.name }}</button>
          </div>
          <div class="form-grid">
            <label class="field">
              <span>Paso que falla</span>
              <select [(ngModel)]="scenario.step">
                <option value="">Ninguno (camino feliz)</option>
                <option value="document">1 · Documento</option>
                <option value="cm">2 · Content Manager</option>
                <option value="insurer">3 · Aseguradora</option>
              </select>
            </label>
            <label class="field" *ngIf="scenario.step">
              <span>Tipo de fallo</span>
              <select [(ngModel)]="scenario.kind">
                <option value="transient">Transitorio (503, se reintenta)</option>
                <option value="permanent">Permanente (422, no se recupera)</option>
              </select>
            </label>
            <label class="field" *ngIf="scenario.step && scenario.kind === 'transient'">
              <span>Intentos fallidos (1–20)</span>
              <input type="number" min="1" max="20" [(ngModel)]="scenario.times">
            </label>
            <label class="field">
              <span>Cantidad de ventas</span>
              <input type="number" min="1" max="1000" [(ngModel)]="count">
            </label>
            <label class="field">
              <span>Envío</span>
              <select [(ngModel)]="mode">
                <option value="api">Por API pública (uno a uno, aplica throttling)</option>
                <option value="burst">Ráfaga: todas de una vez al outbox (sin throttling)</option>
              </select>
            </label>
          </div>
          <div class="prediction">
            <p class="node-title">Resultado esperado por venta</p>
            <p>{{ prediction }}</p>
          </div>
          <button class="primary-button" type="button" (click)="launch()" [disabled]="busy || !validScenario">
            <span *ngIf="busy" class="spinner"></span>{{ busy ? progressText : 'Lanzar ' + count + ' venta(s)' }}
          </button>
          <p class="muted" *ngIf="lastRun">{{ lastRun }}</p>
          <details class="cli">
            <summary>Equivalente por CLI</summary>
            <code>{{ cliCommand }}</code>
          </details>
      </details>

      <!-- ===== 02 Trabajo por paso (cerrado por defecto) ===== -->
      <details class="card panel">
        <summary class="section-heading">
          <div><p class="eyebrow">TRABAJO POR PASO</p><h2>Espera, CPU, memoria y documento</h2></div>
          <span class="step-number">02</span>
        </summary>
          <div class="form-grid" *ngIf="settings">
            <label class="field"><span>Espera mínima (ms)</span><input type="number" min="0" max="15000" step="100" [(ngModel)]="settings.stepDelayMinMs"></label>
            <label class="field"><span>Espera máxima (ms)</span><input type="number" min="0" max="15000" step="100" [(ngModel)]="settings.stepDelayMaxMs"></label>
            <label class="field"><span>CPU por paso (ms, 0–3000)</span><input type="number" min="0" max="3000" step="50" [(ngModel)]="settings.stepCpuMs"></label>
            <label class="field"><span>Memoria por paso (MB, 0–64)</span><input type="number" min="0" max="64" step="4" [(ngModel)]="settings.stepMemoryMb"></label>
            <label class="field"><span>Documento mínimo (KB)</span><input type="number" min="0" max="10240" step="50" [(ngModel)]="settings.documentMinKb"></label>
            <label class="field"><span>Documento máximo (KB)</span><input type="number" min="0" max="10240" step="50" [(ngModel)]="settings.documentMaxKb"></label>
          </div>
          <div class="presets">
            <button type="button" class="chip" (click)="setWork(0, 0, 100, 8)">Por defecto: sin espera, 100 ms CPU, 8 MB</button>
            <button type="button" class="chip" (click)="setWork(0, 0, 0, 0)">Mínimo (solo documento)</button>
            <button type="button" class="chip" (click)="setWork(500, 3000, 200, 16)">Realista: 0.5–3 s + 200 ms CPU + 16 MB</button>
            <button type="button" class="chip" (click)="setWork(0, 500, 800, 32)">CPU intensivo: 800 ms CPU + 32 MB</button>
            <button type="button" class="chip" (click)="setWork(0, 10000, 100, 8)">Espera 0–10 s</button>
          </div>
          <button class="secondary-button" type="button" (click)="saveSettings()" [disabled]="!settings || busy">Guardar</button>
          <details class="info">
            <summary>Cómo funciona</summary>
            <p class="muted">Simula trabajo real en documento, CM y aseguradora: <b>espera</b> de E/S (no usa CPU), <b>CPU</b> en milisegundos de CPU real
              (si el nodo está saturado el paso tarda más) y <b>memoria</b> retenida durante el paso. Aplica al instante (<code>process.lab_settings</code>).
              Espera máx + CPU ≤ 16 000 ms (el paso expira a los 20 s).</p>
            <p class="muted">El documento (crear + firmar SHA256withRSA, subir a S3, descargar y enviar a CM) pesa de verdad entre el mínimo y el máximo:
              esa memoria y esa transferencia son reales, además de la CPU/memoria simulada.</p>
            <div class="prediction">
              <p class="node-title">Cómo se encadenan los reintentos</p>
              <p>Cada entrega de SQS ejecuta una Step Function. Cada paso tiene {{ attempts }} intentos ante 503/timeout.
                Si el paso agota sus intentos, el mensaje se libera y vuelve en {{ releaseDelay }} s (reintento global).
                Tras {{ deliveries }} entregas va a la DLQ y la venta queda FAILED. Un fallo permanente salta directo al reintento global.</p>
              <p class="node-title">Cuándo ayudan más pods</p>
              <p>La concurrencia la fija MaxConcurrency (Lambda). Más pods solo aceleran si el paso consume CPU: con CPU por paso &gt; 0,
                un nodo saturado alarga cada paso; KEDA reparte y Karpenter agrega CPU. Sube <code>API_CPU_REQUEST</code> (bootstrap) para que los pods se repartan entre nodos.</p>
            </div>
          </details>
      </details>

      <!-- ===== Ventas ===== -->
      <section class="card sales-card">
        <div class="section-heading table-heading">
          <div>
            <p class="eyebrow">SEGUIMIENTO</p>
            <h2>Últimas ventas <span class="count">{{ sales.length }}</span></h2>
          </div>
          <div class="list-controls">
            <label class="inline-field">Mostrar
              <select [(ngModel)]="limit" (change)="refresh()"><option *ngFor="let n of limits" [ngValue]="n">{{ n }}</option></select>
            </label>
            <label class="inline-field"><input type="checkbox" [(ngModel)]="autoRefresh" (change)="schedule()"> Auto 3 s</label>
            <button class="secondary-button" type="button" (click)="refresh()">Actualizar</button>
            <button class="check-button" type="button" (click)="toggleAll()" [disabled]="!sales.length">{{ expanded.size ? 'Contraer' : 'Expandir' }} todo</button>
          </div>
        </div>

        <div class="sale-list" *ngIf="sales.length; else emptyState">
          <article class="sale" *ngFor="let sale of sales; trackBy: bySaleId" [class.open]="expanded.has(sale.saleId)">
            <div class="sale-row" role="button" tabindex="0" (click)="toggle(sale.saleId)" (keydown.enter)="toggle(sale.saleId)">
              <span class="chevron">{{ expanded.has(sale.saleId) ? '▾' : '▸' }}</span>
              <span class="reference-cell">{{ sale.externalReference }}</span>
              <span class="status" [ngClass]="statusClass(sale.status)">{{ sale.status }}</span>
              <span class="progress">{{ progress(sale) }}</span>
              <span class="muted">{{ sale.createdAt | date:'HH:mm:ss' }} · {{ elapsed(sale) }}</span>
              <button *ngIf="sale.status === 'FAILED'" class="check-button" type="button" (click)="retry(sale, $event)">Reintentar</button>
            </div>

            <div class="tree" *ngIf="expanded.has(sale.saleId)">
              <ul>
                <li>
                  <span class="node-title">Venta registrada</span>
                  <span class="muted"><code>{{ sale.saleId }}</code> · {{ sale.createdAt | date:'HH:mm:ss.SSS' }}</span>
                  <ul>
                    <li *ngFor="let g of generations(sale)">
                      <span class="node-title">{{ g.event.generation === 1 ? 'Envío original' : 'Reintento manual ' + (g.event.generation - 1) }}</span>
                      <span class="muted">evento creado {{ offset(sale, g.event.createdAt) }}</span>
                      <ul>
                        <li>
                          <span class="node-title">Outbox → SQS</span>
                          <span class="status" [ngClass]="g.event.publishedAt ? 'completed' : 'processing'">{{ g.event.publishedAt ? 'PUBLICADO' : 'PENDIENTE' }}</span>
                          <span class="muted" *ngIf="g.event.publishedAt">{{ offset(sale, g.event.publishedAt) }} · {{ g.event.attempts }} intento(s)</span>
                          <small class="row-error" *ngIf="g.event.error">{{ g.event.error }}</small>
                        </li>
                        <li *ngFor="let a of g.attempts">
                          <span class="node-title">{{ label(a.step) }}</span>
                          <span class="status" [ngClass]="outcomeClass(a.outcome)">{{ a.outcome }}</span>
                          <span class="muted">{{ offset(sale, a.startedAt) }} · espera {{ seconds(a.delayMs) }}
                            <ng-container *ngIf="a.cpuMs"> · CPU {{ a.cpuMs }} ms</ng-container><ng-container *ngIf="a.memoryMb"> · {{ a.memoryMb }} MB</ng-container>
                            <ng-container *ngIf="a.durationMs != null"> · total {{ seconds(a.durationMs) }}</ng-container></span>
                          <small class="row-error" *ngIf="a.detail">{{ a.detail }}</small>
                        </li>
                        <li *ngIf="!g.attempts.length && g.event.publishedAt" class="muted">Esperando a Step Functions…</li>
                      </ul>
                    </li>
                  </ul>
                </li>
                <li>
                  <span class="node-title">Estado actual</span>
                  <span class="status" [ngClass]="statusClass(sale.status)">{{ sale.status }}</span>
                  <span class="muted" *ngIf="sale.updatedAt">{{ offset(sale, sale.updatedAt) }}</span>
                  <small class="row-error" *ngIf="sale.error">{{ sale.error }}</small>
                </li>
              </ul>
            </div>
          </article>
        </div>
        <ng-template #emptyState>
          <div class="empty-state"><div class="empty-icon">↗</div><h3>Aún no hay ventas</h3><p>Lanza un escenario arriba o usa <code>aws/load.sh</code>.</p></div>
        </ng-template>
      </section>

      <div class="toast" *ngIf="message" [class.error]="messageKind === 'error'" role="status">
        <span>{{ message }}</span>
        <button type="button" aria-label="Cerrar mensaje" (click)="message = ''">×</button>
      </div>
    </main>
  `,
  styleUrl: './app.component.css'
})
export class AppComponent implements OnInit, OnDestroy {
  readonly attempts = STEP_ATTEMPTS;
  readonly deliveries = DELIVERIES;
  readonly releaseDelay = RELEASE_DELAY_S;
  readonly presets: Preset[] = [
    { name: 'Camino feliz', step: '', kind: 'transient', times: 1 },
    { name: 'Reintento de paso (CM falla 2)', step: 'cm', kind: 'transient', times: 2 },
    { name: 'Reintento global (Documento falla 3)', step: 'document', kind: 'transient', times: 3 },
    { name: 'Éxito en la última entrega (Aseguradora falla 8)', step: 'insurer', kind: 'transient', times: 8 },
    { name: 'Agota todo → DLQ (CM falla 9)', step: 'cm', kind: 'transient', times: 9 },
    { name: 'Permanente → DLQ (Aseguradora)', step: 'insurer', kind: 'permanent', times: 1 }
  ];

  system?: SystemView;
  systemError = '';
  settings?: LabSettings;
  sales: SaleView[] = [];
  expanded = new Set<string>();
  limits = [20, 50, 100, 200];
  limit = 50;
  autoRefresh = true;
  scenario: Scenario = { step: '', kind: 'transient', times: 1 };
  count = 1;
  mode: 'api' | 'burst' = 'api';
  busy = false;
  progressText = '';
  lastRun = '';
  message = '';
  messageKind: 'success' | 'error' = 'success';
  private timer?: ReturnType<typeof setInterval>;
  private polling = false;

  constructor(private readonly api: SalesApiService) {}

  ngOnInit(): void {
    this.api.settings().then(s => this.settings = s).catch(e => this.notify(this.errorMessage(e), 'error'));
    this.refresh();
    this.schedule();
  }

  ngOnDestroy(): void {
    clearInterval(this.timer);
  }

  schedule(): void {
    clearInterval(this.timer);
    if (this.autoRefresh) this.timer = setInterval(() => this.refresh(), 3000);
  }

  async refresh(): Promise<void> {
    if (this.polling) return;
    this.polling = true;
    try {
      const [sales, system] = await Promise.allSettled([this.api.list(this.limit), this.api.system()]);
      if (sales.status === 'fulfilled') this.sales = sales.value;
      if (system.status === 'fulfilled') { this.system = system.value; this.systemError = ''; }
      else this.systemError = this.errorMessage(system.reason);
    } finally {
      this.polling = false;
    }
  }

  // ---------- Scenario ----------
  get failure(): string {
    const s = this.scenario;
    return s.step ? `${s.step}:${s.kind}:${s.kind === 'permanent' ? 1 : s.times}` : '';
  }

  get validScenario(): boolean {
    const timesOk = !this.scenario.step || this.scenario.kind === 'permanent' || (this.scenario.times >= 1 && this.scenario.times <= 20);
    return timesOk && this.count >= 1 && this.count <= 1000;
  }

  get prediction(): string {
    const s = this.scenario;
    if (!s.step) return 'COMPLETED en la entrega 1, sin reintentos.';
    const step = STEP_LABELS[s.step];
    if (s.kind === 'permanent') {
      return `${step} responde 422 en cada entrega: sin reintento de paso. Tras ${DELIVERIES} entregas (~${(DELIVERIES - 1) * RELEASE_DELAY_S} s) → FAILED y mensaje en DLQ. "Reintentar" crea un envío nuevo sin el fallo.`;
    }
    const delivery = Math.floor(s.times / STEP_ATTEMPTS) + 1;
    if (delivery > DELIVERIES) {
      return `${step} falla ${s.times} veces: agota ${STEP_ATTEMPTS} intentos × ${DELIVERIES} entregas → FAILED y mensaje en DLQ. Un redrive lo reprocesa${s.times > STEP_ATTEMPTS * DELIVERIES + STEP_ATTEMPTS ? ' (seguirá fallando ' + (s.times - STEP_ATTEMPTS * DELIVERIES) + ' veces)' : ''}.`;
    }
    const attempt = (s.times % STEP_ATTEMPTS) + 1;
    return delivery === 1
      ? `${step} falla ${s.times} vez/veces y se recupera con el reintento de paso (intento ${attempt} de la entrega 1). COMPLETED sin reintento global.`
      : `${step} agota sus intentos en ${delivery - 1} entrega(s); reintento global tras ${RELEASE_DELAY_S} s → se recupera en la entrega ${delivery}, intento ${attempt}. COMPLETED.`;
  }

  get cliCommand(): string {
    const f = this.failure ? ` -f ${this.failure}` : '';
    return this.mode === 'burst'
      ? `curl -s -X POST "$SITE/api/v1/lab/burst" -H 'Content-Type: application/json' -d '{"count":${this.count}${this.failure ? `,"failure":"${this.failure}"` : ''}}'`
      : `aws/load.sh -n ${this.count} -c ${Math.min(4, this.count)}${f}`;
  }

  apply(p: Preset): void {
    this.scenario = { step: p.step, kind: p.kind, times: p.times };
  }

  isPreset(p: Preset): boolean {
    const s = this.scenario;
    return s.step === p.step && (!s.step || (s.kind === p.kind && (s.kind === 'permanent' || s.times === p.times)));
  }

  async launch(): Promise<void> {
    this.busy = true;
    this.lastRun = '';
    const failure = this.failure;
    const run = new Date().toTimeString().slice(0, 8).replace(/:/g, '');
    try {
      if (this.mode === 'burst') {
        this.progressText = 'Insertando en el outbox…';
        const r = await this.api.burst(this.count, failure);
        this.lastRun = `Ráfaga ${r.prefix}: ${r.count} ventas aceptadas en una transacción. Mira Outbox → Cola → Pods → Nodos.`;
      } else {
        const codes = new Map<string, number>();
        let next = 0;
        const worker = async () => {
          while (next < this.count) {
            const i = ++next;
            this.progressText = `Enviando ${i}/${this.count}…`;
            try {
              await this.api.create({ externalReference: `UI-${run}-${i}`, customerName: `Lab ${i}`,
                customerEmail: `lab${i}@example.com`, productCode: 'AUTO-01' }, failure);
              codes.set('202', (codes.get('202') ?? 0) + 1);
            } catch (e) {
              const code = e instanceof HttpErrorResponse ? String(e.status) : 'error';
              codes.set(code, (codes.get(code) ?? 0) + 1);
            }
          }
        };
        await Promise.all(Array.from({ length: Math.min(4, this.count) }, worker));
        this.lastRun = `UI-${run}: ` + [...codes].map(([c, n]) => `${n}× ${c}`).join(' · ') + (codes.has('429') ? ' (429 = throttling de API Gateway)' : '');
      }
      await this.refresh();
    } catch (e) {
      this.notify(this.errorMessage(e), 'error');
    } finally {
      this.busy = false;
    }
  }

  // ---------- Settings / DLQ / retry ----------
  setWork(min: number, max: number, cpu: number, memory: number): void {
    this.settings = { stepDelayMinMs: min, stepDelayMaxMs: max, stepCpuMs: cpu, stepMemoryMb: memory,
      documentMinKb: this.settings?.documentMinKb ?? 200, documentMaxKb: this.settings?.documentMaxKb ?? 500 };
    this.saveSettings();
  }

  async saveSettings(): Promise<void> {
    if (!this.settings) return;
    try {
      this.settings = await this.api.saveSettings(this.settings);
      const s = this.settings;
      this.notify(`Por paso: espera ${s.stepDelayMinMs}–${s.stepDelayMaxMs} ms · CPU ${s.stepCpuMs} ms · ${s.stepMemoryMb} MB · documento ${s.documentMinKb}–${s.documentMaxKb} KB`);
    } catch (e) {
      this.notify(this.errorMessage(e), 'error');
    }
  }

  async redrive(): Promise<void> {
    this.busy = true;
    try {
      await this.api.redrive();
      this.notify('Redrive iniciado: los mensajes de la DLQ vuelven a la cola principal.');
    } catch (e) {
      this.notify(this.errorMessage(e), 'error');
    } finally {
      this.busy = false;
    }
  }

  async retry(sale: SaleView, event: Event): Promise<void> {
    event.stopPropagation();
    try {
      await this.api.retry(sale.saleId);
      this.expanded.add(sale.saleId);
      this.notify(`Reintento manual de ${sale.externalReference}: nuevo envío sin fallo inducido.`);
      await this.refresh();
    } catch (e) {
      this.notify(this.errorMessage(e), 'error');
    }
  }

  // ---------- Sales tree ----------
  toggle(saleId: string): void {
    if (!this.expanded.delete(saleId)) this.expanded.add(saleId);
  }

  toggleAll(): void {
    this.expanded = this.expanded.size ? new Set() : new Set(this.sales.map(s => s.saleId));
  }

  bySaleId(_: number, sale: SaleView): string {
    return sale.saleId;
  }

  generations(sale: SaleView): TreeGeneration[] {
    return sale.outbox.map((event, i) => {
      const from = Date.parse(event.createdAt);
      const next = sale.outbox[i + 1];
      const to = next ? Date.parse(next.createdAt) : Infinity;
      return { event, attempts: sale.history.filter(a => Date.parse(a.startedAt) >= from && Date.parse(a.startedAt) < to) };
    });
  }

  progress(sale: SaleView): string {
    const done = new Set(sale.history.filter(a => this.outcomeClass(a.outcome) === 'completed').map(a => a.step));
    const running = sale.history.find(a => a.outcome === 'RUNNING');
    const errors = sale.history.filter(a => a.outcome === 'ERROR').length;
    const steps = ['document', 'cm', 'insurer'].filter(s => done.has(s)).length;
    const base = running ? `${steps}/3 · en curso: ${STEP_LABELS[running.step] ?? running.step}` : `${steps}/3 pasos`;
    return errors ? `${base} · ${errors} error(es)` : base;
  }

  elapsed(sale: SaleView): string {
    const end = sale.status === 'COMPLETED' || sale.status === 'FAILED' ? Date.parse(sale.updatedAt ?? sale.createdAt) : Date.now();
    return this.seconds(end - Date.parse(sale.createdAt));
  }

  offset(sale: SaleView, at: string): string {
    return `+${this.seconds(Date.parse(at) - Date.parse(sale.createdAt))}`;
  }

  seconds(ms: number): string {
    return `${(Math.max(0, ms) / 1000).toFixed(1)} s`;
  }

  label(step: string): string {
    return STEP_LABELS[step] ?? step;
  }

  nodeGroups(s: SystemView): string[] {
    const groups = new Map<string, number>();
    (s.kubernetes.nodes ?? []).forEach(n => {
      const key = `${n.pool} · ${n.capacityType} · ${n.instanceType}${n.ready ? '' : ' (no listo)'}`;
      groups.set(key, (groups.get(key) ?? 0) + 1);
    });
    return [...groups].map(([k, n]) => `${n}× ${k}`);
  }

  entries(map: Record<string, number>): { key: string; value: number }[] {
    return Object.entries(map).map(([key, value]) => ({ key, value }));
  }

  statusClass(status: string): string {
    return status.toLowerCase();
  }

  outcomeClass(outcome: string): string {
    if (['DONE', 'RECONCILED', 'ALREADY_DONE', 'COMPLETED'].includes(outcome)) return 'completed';
    if (outcome === 'RUNNING') return 'processing';
    return 'failed';
  }

  private errorMessage(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      const body = error.error as { message?: string; error?: string } | null;
      if (error.status === 0) return 'No se pudo conectar con la API.';
      if (error.status === 404) return 'Ruta no disponible: actualiza el stack 06 y la imagen de la API.';
      return `HTTP ${error.status}: ${body?.message ?? body?.error ?? error.statusText}`;
    }
    return error instanceof Error ? error.message : 'No se pudo completar la solicitud.';
  }

  private notify(message: string, kind: 'success' | 'error' = 'success'): void {
    this.message = message;
    this.messageKind = kind;
  }
}
