import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

export interface SaleRequest {
  externalReference: string;
  customerName: string;
  customerEmail: string;
  productCode: string;
}

export interface SaleStatus {
  saleId: string;
  status: string;
  updatedAt?: string;
  error?: string | null;
  downloadUrl?: string;
}

@Injectable({ providedIn: 'root' })
export class SalesApiService {
  private readonly http = inject(HttpClient);

  async create(baseUrl: string, token: string, sale: SaleRequest): Promise<SaleStatus> {
    return firstValueFrom(this.http.post<SaleStatus>(
      this.apiUrl(baseUrl, '/purchases'),
      sale,
      { headers: this.headers(token) }
    ));
  }

  async status(baseUrl: string, token: string, saleId: string): Promise<SaleStatus> {
    return firstValueFrom(this.http.get<SaleStatus>(
      this.apiUrl(baseUrl, `/purchases/${encodeURIComponent(saleId)}`),
      { headers: this.headers(token) }
    ));
  }

  private headers(token: string): HttpHeaders {
    return new HttpHeaders({
      Authorization: `Bearer ${token.trim()}`,
      'Content-Type': 'application/json'
    });
  }

  private apiUrl(baseUrl: string, resource: string): string {
    const value = baseUrl.trim().replace(/\/+$/, '');
    if (!value) {
      throw new Error('Configura la URL base de API Gateway.');
    }

    if (value.startsWith('/_aws/execute-api/')) {
      return `/gateway${value}${resource}`;
    }

    let url: URL;
    try {
      url = new URL(value);
    } catch {
      throw new Error('La URL debe ser http://127.0.0.1:4566/_aws/execute-api/<api-id>/lab.');
    }

    if (!['127.0.0.1', 'localhost'].includes(url.hostname) || url.port !== '4566'
      || !url.pathname.startsWith('/_aws/execute-api/')) {
      throw new Error('Usa el endpoint local de API Gateway generado en purchase.http.');
    }

    const apiPath = `${url.pathname}${resource}`;
    const sameOriginTunnel = window.location.pathname.startsWith('/micro-test-local-frontend/')
      && window.location.hostname === url.hostname;
    if (window.location.origin === url.origin || sameOriginTunnel) {
      return apiPath;
    }
    if (window.location.port === '4200') {
      return `/gateway${apiPath}`;
    }
    return `${url.origin}${apiPath}`;
  }
}
