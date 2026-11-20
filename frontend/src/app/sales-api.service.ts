import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

/** Same origin: CloudFront serves the SPA and routes /api/* to API Gateway (AWS lab). */
const API = '/api/v1';

export interface SaleRequest {
  externalReference: string;
  customerName: string;
  customerEmail: string;
  productCode: string;
}

export interface StepAttempt {
  step: string;
  outcome: string;
  detail?: string | null;
  delayMs: number;
  cpuMs?: number;
  memoryMb?: number;
  durationMs?: number | null;
  startedAt: string;
  finishedAt?: string | null;
}

export interface OutboxEvent {
  generation: number;
  createdAt: string;
  publishedAt?: string | null;
  attempts: number;
  error?: string | null;
}

export interface SaleView {
  saleId: string;
  externalReference: string;
  status: string;
  generation: number;
  error?: string | null;
  createdAt: string;
  updatedAt?: string;
  outbox: OutboxEvent[];
  history: StepAttempt[];
}

export interface LabSettings {
  stepDelayMinMs: number;
  stepDelayMaxMs: number;
  stepCpuMs: number;
  stepMemoryMb: number;
  documentMinKb: number;
  documentMaxKb: number;
}

export interface QueueView {
  configured: boolean;
  name?: string;
  visible?: number;
  inFlight?: number;
  delayed?: number;
  error?: string;
}

export interface SystemView {
  queues: { main: QueueView; dlq: QueueView };
  outboxPending: number;
  sales: Record<string, number>;
  running: number;
  kubernetes: {
    available: boolean;
    error?: string;
    deployments?: { name: string; desired: number; ready: number }[];
    pendingPods?: Record<string, number>;
    hpa?: { name: string; current: number; desired: number; min: number; max: number }[];
    nodes?: { name: string; pool: string; capacityType: string; instanceType: string; ready: boolean }[];
  };
  limits: Record<string, number | string>;
}

@Injectable({ providedIn: 'root' })
export class SalesApiService {
  private readonly http = inject(HttpClient);

  create(sale: SaleRequest, failure?: string): Promise<{ saleId: string; status: string }> {
    const headers: Record<string, string> = failure ? { 'X-Lab-Failure': failure } : {};
    return firstValueFrom(this.http.post<{ saleId: string; status: string }>(`${API}/purchases`, sale, { headers }));
  }

  list(limit: number): Promise<SaleView[]> {
    return firstValueFrom(this.http.get<SaleView[]>(`${API}/purchases?limit=${limit}`));
  }

  retry(saleId: string): Promise<unknown> {
    return firstValueFrom(this.http.post(`${API}/purchases/${encodeURIComponent(saleId)}/retry`, {}));
  }

  settings(): Promise<LabSettings> {
    return firstValueFrom(this.http.get<LabSettings>(`${API}/lab/settings`));
  }

  saveSettings(settings: LabSettings): Promise<LabSettings> {
    return firstValueFrom(this.http.put<LabSettings>(`${API}/lab/settings`, settings));
  }

  system(): Promise<SystemView> {
    return firstValueFrom(this.http.get<SystemView>(`${API}/lab/system`));
  }

  burst(count: number, failure?: string): Promise<{ prefix: string; count: number }> {
    return firstValueFrom(this.http.post<{ prefix: string; count: number }>(`${API}/lab/burst`, { count, failure: failure || null }));
  }

  redrive(): Promise<unknown> {
    return firstValueFrom(this.http.post(`${API}/lab/dlq/redrive`, {}));
  }
}
