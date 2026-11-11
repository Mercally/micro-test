import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SaleRequest, SaleStatus, SalesApiService } from './sales-api.service';

interface SaleRow {
  saleId: string;
  externalReference: string;
  status: string;
  updatedAt?: string;
  error?: string | null;
  downloadUrl?: string;
}

const SALES_KEY = 'micro-test.sales';
const API_URL_KEY = 'micro-test.api-url';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <main class="page-shell">
      <header class="topbar">
        <div>
          <p class="eyebrow">LABORATORIO LOCAL</p>
          <h1>Ventas</h1>
          <p class="subtitle">Crea una venta y sigue su procesamiento asíncrono.</p>
        </div>
        <span class="auth-badge"><span></span> JWT protegido</span>
      </header>

      <section class="config-card">
        <div class="section-heading">
          <div>
            <p class="eyebrow">CONEXIÓN</p>
            <h2>API Gateway y autenticación</h2>
          </div>
          <span class="local-badge">LocalStack</span>
        </div>
        <div class="config-grid">
          <label class="field">
            <span>URL base de API Gateway</span>
            <input [(ngModel)]="apiUrl" (change)="saveApiUrl()"
              placeholder="http://127.0.0.1:4566/_aws/execute-api/<api-id>/lab">
            <small>Copia <code>&#64;baseUrl</code> desde <code>localstack/.state/business/purchase.http</code>.</small>
          </label>
          <label class="field">
            <span>JWT de práctica</span>
            <input [(ngModel)]="token" type="password" autocomplete="off"
              placeholder="Pega el valor de @token, sin la palabra Bearer">
            <small>Se conserva solo en memoria; no se guarda en el navegador.</small>
          </label>
        </div>
      </section>

      <section class="content-grid">
        <section class="card sale-form-card">
          <div class="section-heading">
            <div>
              <p class="eyebrow">NUEVA OPERACIÓN</p>
              <h2>Crear venta</h2>
            </div>
            <span class="step-number">01</span>
          </div>
          <form (ngSubmit)="createSale()" #saleForm="ngForm">
            <label class="field">
              <span>Referencia externa</span>
              <input name="externalReference" [(ngModel)]="draft.externalReference"
                required maxlength="120" placeholder="Ej. WEB-2026-001">
            </label>
            <label class="field">
              <span>Nombre del cliente</span>
              <input name="customerName" [(ngModel)]="draft.customerName"
                required maxlength="200" placeholder="Nombre completo">
            </label>
            <label class="field">
              <span>Correo electrónico</span>
              <input name="customerEmail" [(ngModel)]="draft.customerEmail"
                type="email" required maxlength="320" placeholder="cliente@ejemplo.com">
            </label>
            <label class="field">
              <span>Código de producto</span>
              <input name="productCode" [(ngModel)]="draft.productCode"
                required maxlength="80" placeholder="AUTO-01">
            </label>
            <button class="primary-button" type="submit"
              [disabled]="saleForm.invalid || creating || !credentialsReady">
              <span *ngIf="creating" class="spinner"></span>
              {{ creating ? 'Enviando…' : 'Crear venta' }}
            </button>
            <p class="hint" *ngIf="!credentialsReady">Completa la URL y el JWT para habilitar el envío.</p>
          </form>
        </section>

        <section class="card sales-card">
          <div class="section-heading table-heading">
            <div>
              <p class="eyebrow">SEGUIMIENTO</p>
              <h2>Ventas registradas <span class="count">{{ sales.length }}</span></h2>
            </div>
            <button class="secondary-button" type="button" (click)="refreshAll()"
              [disabled]="refreshingAll || !credentialsReady || !sales.length">
              {{ refreshingAll ? 'Consultando…' : 'Actualizar todas' }}
            </button>
          </div>
          <div class="table-wrap" *ngIf="sales.length; else emptyState">
            <table>
              <thead>
                <tr>
                  <th>Referencia</th>
                  <th>Venta</th>
                  <th>Estado</th>
                  <th>Actualizada</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr *ngFor="let sale of sales">
                  <td class="reference-cell">{{ sale.externalReference }}</td>
                  <td><code>{{ sale.saleId }}</code></td>
                  <td><span class="status" [ngClass]="statusClass(sale.status)">{{ sale.status }}</span>
                    <small class="row-error" *ngIf="sale.error">{{ sale.error }}</small></td>
                  <td>{{ sale.updatedAt ? (sale.updatedAt | date:'short') : '—' }}</td>
                  <td>
                    <button class="check-button" type="button" (click)="refreshSale(sale)"
                      [disabled]="checking[sale.saleId] || !credentialsReady">
                      {{ checking[sale.saleId] ? '…' : 'Consultar' }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <ng-template #emptyState>
            <div class="empty-state">
              <div class="empty-icon">↗</div>
              <h3>Aún no hay ventas</h3>
              <p>Al crear una venta aparecerá aquí para consultar su estado.</p>
            </div>
          </ng-template>
        </section>
      </section>

      <div class="toast" *ngIf="message" [class.error]="messageKind === 'error'" role="status">
        <span>{{ message }}</span>
        <button type="button" aria-label="Cerrar mensaje" (click)="message = ''">×</button>
      </div>
      <footer>El estado inicial es <strong>ACCEPTED</strong>. El procesamiento puede tardar hasta un minuto.</footer>
    </main>
  `,
  styleUrl: './app.component.css'
})
export class AppComponent {
  apiUrl = localStorage.getItem(API_URL_KEY) ?? '';
  token = '';
  sales: SaleRow[] = this.readSales();
  creating = false;
  refreshingAll = false;
  checking: Record<string, boolean> = {};
  message = '';
  messageKind: 'success' | 'error' = 'success';
  draft: SaleRequest = {
    externalReference: '',
    customerName: '',
    customerEmail: '',
    productCode: 'AUTO-01'
  };

  constructor(private readonly api: SalesApiService) {}

  get credentialsReady(): boolean {
    return Boolean(this.apiUrl.trim() && this.token.trim());
  }

  saveApiUrl(): void {
    localStorage.setItem(API_URL_KEY, this.apiUrl.trim());
  }

  async createSale(): Promise<void> {
    this.creating = true;
    this.clearMessage();
    try {
      const request = { ...this.draft, externalReference: this.draft.externalReference.trim() };
      const created = await this.api.create(this.apiUrl, this.token, request);
      this.upsert({
        saleId: created.saleId,
        externalReference: request.externalReference,
        status: created.status ?? 'ACCEPTED',
        updatedAt: created.updatedAt,
        error: created.error
      });
      this.draft = { externalReference: '', customerName: '', customerEmail: '', productCode: 'AUTO-01' };
      this.notify(`Venta aceptada: ${created.saleId}`);
    } catch (error) {
      this.notify(this.errorMessage(error), 'error');
    } finally {
      this.creating = false;
    }
  }

  async refreshSale(sale: SaleRow): Promise<void> {
    this.checking[sale.saleId] = true;
    this.clearMessage();
    try {
      const result = await this.api.status(this.apiUrl, this.token, sale.saleId);
      this.upsert({ ...sale, ...result });
      this.notify(`Estado actualizado: ${result.status}`);
    } catch (error) {
      this.notify(this.errorMessage(error), 'error');
    } finally {
      this.checking[sale.saleId] = false;
    }
  }

  async refreshAll(): Promise<void> {
    this.refreshingAll = true;
    try {
      for (const sale of [...this.sales]) {
        await this.refreshSale(sale);
      }
    } finally {
      this.refreshingAll = false;
    }
  }

  statusClass(status: string): string {
    return status.toLowerCase();
  }

  private upsert(sale: SaleRow): void {
    const index = this.sales.findIndex(item => item.saleId === sale.saleId);
    this.sales = index < 0
      ? [sale, ...this.sales]
      : this.sales.map(item => item.saleId === sale.saleId ? { ...item, ...sale } : item);
    this.persistSales();
  }

  private readSales(): SaleRow[] {
    try {
      const value: unknown = JSON.parse(localStorage.getItem(SALES_KEY) ?? '[]');
      return Array.isArray(value) ? value as SaleRow[] : [];
    } catch {
      return [];
    }
  }

  private persistSales(): void {
    localStorage.setItem(SALES_KEY, JSON.stringify(this.sales));
  }

  private errorMessage(error: unknown): string {
    if (error instanceof HttpErrorResponse && (error.status === 401 || error.status === 403)) {
      return 'Autenticación rechazada. Genera un JWT nuevo; el token de práctica vence en una hora.';
    }
    if (error instanceof HttpErrorResponse && error.status === 0) {
      return 'No se pudo conectar con LocalStack. Verifica que el laboratorio esté activo y que la URL de API Gateway sea correcta.';
    }
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return 'No se pudo completar la solicitud. Verifica la URL, el JWT y que el laboratorio esté activo.';
  }

  private notify(message: string, kind: 'success' | 'error' = 'success'): void {
    this.message = message;
    this.messageKind = kind;
  }

  private clearMessage(): void {
    this.message = '';
  }
}
