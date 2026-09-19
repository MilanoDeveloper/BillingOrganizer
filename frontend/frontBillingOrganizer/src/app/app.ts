import { afterNextRender, Component, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';

interface Transaction {
  id: number;
  data: string;
  descricao: string;
  valor: number;
  categoria: string;
  origem: string;
  tipo_entrada: string;
}

interface Summary {
  gastos: number;
  entradas: number;
  quantidade: number;
  categorias: { nome: string; total: number }[];
  maiores_gastos: { nome: string; total: number; ocorrencias: number }[];
}

@Component({
  imports: [FormsModule],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {
  private readonly http = inject(HttpClient);
  protected readonly loading = signal(true);
  protected readonly message = signal('');
  protected readonly error = signal('');
  protected readonly transactions = signal<Transaction[]>([]);
  protected readonly summary = signal<Summary>({ gastos: 0, entradas: 0, quantidade: 0, categorias: [], maiores_gastos: [] });
  protected manual = { descricao: '', valor: null as number | null, data: new Date().toISOString().slice(0, 10), categoria: 'Outros', metodo_pagamento: '' };

  constructor() {
    afterNextRender(() => this.loadDashboard());
  }

  protected loadDashboard(): void {
    this.loading.set(true);
    this.http.get<Summary>('/api/resumo').subscribe({
      next: (summary) => this.summary.set(summary),
      error: () => this.error.set('Não foi possível conectar à API. Inicie o backend na porta 8000.'),
      complete: () => this.loading.set(false),
    });
    this.http.get<Transaction[]>('/api/transacoes').subscribe({
      next: (transactions) => this.transactions.set(transactions),
    });
  }

  protected addManual(): void {
    if (!this.manual.descricao.trim() || !this.manual.valor || this.manual.valor <= 0) {
      this.error.set('Informe uma descrição e um valor maior que zero.');
      return;
    }
    this.http.post('/api/transacoes/manual', this.manual).subscribe({
      next: () => {
        this.message.set('Conta adicionada.');
        this.error.set('');
        this.manual = { descricao: '', valor: null, data: new Date().toISOString().slice(0, 10), categoria: 'Outros', metodo_pagamento: '' };
        this.loadDashboard();
      },
      error: () => this.error.set('Não foi possível salvar a conta.'),
    });
  }

  protected importFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    this.http.post<{ message: string }>('/api/importar', formData).subscribe({
      next: (response) => {
        this.message.set(response.message);
        this.error.set('');
        input.value = '';
        this.loadDashboard();
      },
      error: (response) => this.error.set(response.error?.detail ?? 'Não foi possível importar o arquivo.'),
    });
  }

  protected formatCurrency(value: number): string {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);
  }

  protected formatDate(value: string): string {
    return new Intl.DateTimeFormat('pt-BR').format(new Date(`${value}T12:00:00`));
  }
}
