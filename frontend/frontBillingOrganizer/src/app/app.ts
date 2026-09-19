import { afterNextRender, Component, inject, signal } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
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
  protected readonly theme = signal<'dark' | 'light'>('dark');
  protected readonly transactions = signal<Transaction[]>([]);
  protected readonly editingId = signal<number | null>(null);
  protected readonly summary = signal<Summary>({ gastos: 0, entradas: 0, quantidade: 0, categorias: [], maiores_gastos: [] });
  protected dataInicio = '';
  protected dataFim = '';
  protected manual = { descricao: '', valor: null as number | null, data: new Date().toISOString().slice(0, 10), categoria: 'Outros', metodo_pagamento: '' };
  protected editDraft = { descricao: '', categoria: 'Outros' };

  constructor() {
    afterNextRender(() => {
      const savedTheme = localStorage.getItem('billing-theme');
      if (savedTheme === 'light' || savedTheme === 'dark') this.theme.set(savedTheme);
      this.loadDashboard();
    });
  }

  protected toggleTheme(): void {
    const nextTheme = this.theme() === 'dark' ? 'light' : 'dark';
    this.theme.set(nextTheme);
    localStorage.setItem('billing-theme', nextTheme);
  }

  protected loadDashboard(): void {
    this.loading.set(true);
    const params = this.periodParams();
    this.http.get<Summary>('/api/resumo', { params }).subscribe({
      next: (summary) => this.summary.set(summary),
      error: () => this.error.set('Não foi possível conectar à API. Inicie o backend na porta 8000.'),
      complete: () => this.loading.set(false),
    });
    this.http.get<Transaction[]>('/api/transacoes', { params }).subscribe({
      next: (transactions) => this.transactions.set(transactions),
    });
  }

  protected applyDateFilter(): void {
    if ((this.dataInicio && !this.dataFim) || (!this.dataInicio && this.dataFim)) {
      this.error.set('Informe a data inicial e a data final.');
      return;
    }
    if (this.dataInicio && this.dataFim && this.dataInicio > this.dataFim) {
      this.error.set('A data inicial deve ser anterior à data final.');
      return;
    }
    this.error.set('');
    this.message.set(this.dataInicio ? 'Período aplicado.' : 'Exibindo todos os lançamentos.');
    this.loadDashboard();
  }

  protected clearDateFilter(): void {
    this.dataInicio = '';
    this.dataFim = '';
    this.applyDateFilter();
  }

  private periodParams(): HttpParams {
    let params = new HttpParams();
    if (this.dataInicio) params = params.set('data_inicio', this.dataInicio);
    if (this.dataFim) params = params.set('data_fim', this.dataFim);
    return params;
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

  protected startEdit(transaction: Transaction): void {
    this.editingId.set(transaction.id);
    this.editDraft = { descricao: transaction.descricao, categoria: transaction.categoria };
  }

  protected cancelEdit(): void {
    this.editingId.set(null);
  }

  protected saveEdit(transaction: Transaction): void {
    if (!this.editDraft.descricao.trim()) {
      this.error.set('O nome da conta não pode ficar vazio.');
      return;
    }
    this.http.patch(`/api/transacoes/${transaction.id}`, this.editDraft).subscribe({
      next: () => {
        this.message.set('Lançamento atualizado.');
        this.error.set('');
        this.editingId.set(null);
        this.loadDashboard();
      },
      error: () => this.error.set('Não foi possível atualizar o lançamento.'),
    });
  }

  protected formatCurrency(value: number): string {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);
  }

  protected formatDate(value: string): string {
    return new Intl.DateTimeFormat('pt-BR').format(new Date(`${value}T12:00:00`));
  }
}
