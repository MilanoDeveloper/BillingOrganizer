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

interface Category {
  id: number;
  nome: string;
}

interface MonthlyBill {
  name: string;
  paid: boolean;
}

const FIXED_CATEGORY_COLORS = new Map([
  ['Alimentação', '#c87558'],
  ['Contas fixas', '#6a9d78'],
  ['Investimentos', '#d3ae62'],
  ['Lazer', '#8a789e'],
  ['Moradia', '#5b9bb0'],
  ['Outros', '#a5a95e'],
]);

const FIXED_CATEGORY_HUES = new Map([
  ['Alimentação', 14],
  ['Contas fixas', 137],
  ['Investimentos', 42],
  ['Lazer', 267],
  ['Moradia', 193],
  ['Outros', 63],
]);

@Component({
  imports: [FormsModule],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {
  private readonly http = inject(HttpClient);
  private readonly assignedCategoryColors = new Map<string, string>();
  private readonly assignedCategoryHues = new Map<string, number>();
  protected readonly loading = signal(true);
  protected readonly authenticated = signal(false);
  protected readonly authMode = signal<'login' | 'register'>('login');
  protected readonly authLoading = signal(false);
  protected readonly message = signal('');
  protected readonly error = signal('');
  protected readonly theme = signal<'dark' | 'light'>('dark');
  protected readonly transactionsExpanded = signal(true);
  protected readonly draggingFile = signal(false);
  protected readonly transactions = signal<Transaction[]>([]);
  protected readonly categories = signal<Category[]>([]);
  protected readonly editingId = signal<number | null>(null);
  protected readonly summary = signal<Summary>({ gastos: 0, entradas: 0, quantidade: 0, categorias: [], maiores_gastos: [] });
  protected readonly checklistMonth = signal(this.currentMonth());
  protected readonly monthlyBills = signal<MonthlyBill[]>(this.defaultBills().map((name) => ({ name, paid: false })));
  private readonly checklistUserId = signal<number | null>(null);
  protected dataInicio = '';
  protected dataFim = '';
  protected searchTerm = '';
  protected manual = { descricao: '', valor: null as number | null, data: new Date().toISOString().slice(0, 10), categoria: 'Outros', metodo_pagamento: '' };
  protected editDraft = { descricao: '', categoria: 'Outros' };
  protected newCategory = '';
  protected authForm = { nome: '', email: '', senha: '' };

  constructor() {
    afterNextRender(() => {
      const savedTheme = localStorage.getItem('billing-theme');
      if (savedTheme === 'light' || savedTheme === 'dark') this.theme.set(savedTheme);
      this.restoreSession();
    });
  }

  protected restoreSession(): void {
    this.http.get<{ id: number }>('/api/auth/me').subscribe({
      next: (user) => {
        this.authenticated.set(true);
        this.checklistUserId.set(user.id);
        this.loadMonthlyChecklist();
        this.loadDashboard();
        this.loadCategories();
      },
      error: () => this.loading.set(false),
    });
  }

  protected submitAuth(): void {
    this.authLoading.set(true);
    this.error.set('');
    const endpoint = this.authMode() === 'login' ? '/api/auth/login' : '/api/auth/cadastro';
    this.http.post<{ id: number }>(endpoint, this.authForm).subscribe({
      next: (user) => {
        this.authenticated.set(true);
        this.checklistUserId.set(user.id);
        this.loadMonthlyChecklist();
        this.authLoading.set(false);
        this.authForm = { nome: '', email: '', senha: '' };
        this.loadDashboard();
        this.loadCategories();
      },
      error: (response) => {
        this.authLoading.set(false);
        this.error.set(response.error?.detail ?? 'Não foi possível autenticar.');
      },
    });
  }

  protected logout(): void {
    this.http.post('/api/auth/logout', {}).subscribe({
      next: () => {
        this.authenticated.set(false);
        this.authMode.set('login');
        this.message.set('Sessão encerrada.');
      },
    });
  }

  protected toggleTheme(): void {
    const nextTheme = this.theme() === 'dark' ? 'light' : 'dark';
    this.theme.set(nextTheme);
    localStorage.setItem('billing-theme', nextTheme);
  }

  protected toggleTransactions(): void {
    this.transactionsExpanded.update((expanded) => !expanded);
  }

  protected changeChecklistMonth(offset: number): void {
    const [year, month] = this.checklistMonth().split('-').map(Number);
    const date = new Date(year, month - 1 + offset, 1);
    this.checklistMonth.set(`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`);
    this.loadMonthlyChecklist();
  }

  protected checklistMonthLabel(): string {
    const [year, month] = this.checklistMonth().split('-').map(Number);
    return new Intl.DateTimeFormat('pt-BR', { month: 'long', year: 'numeric' }).format(new Date(year, month - 1, 1));
  }

  protected completedBills(): number {
    return this.monthlyBills().filter((bill) => bill.paid).length;
  }

  protected checklistProgressPercent(): number {
    const total = this.monthlyBills().length;
    return total ? Math.round((this.completedBills() / total) * 100) : 0;
  }

  protected toggleBill(name: string, event: Event): void {
    const paid = (event.target as HTMLInputElement).checked;
    const bills = this.monthlyBills().map((bill) => bill.name === name ? { ...bill, paid } : bill);
    this.monthlyBills.set(bills);
    const userId = this.checklistUserId();
    if (userId !== null) {
      const paidBills = Object.fromEntries(bills.map((bill) => [bill.name, bill.paid]));
      localStorage.setItem(this.checklistStorageKey(userId, this.checklistMonth()), JSON.stringify(paidBills));
    }
  }

  private loadMonthlyChecklist(): void {
    const userId = this.checklistUserId();
    const saved = userId === null ? null : localStorage.getItem(this.checklistStorageKey(userId, this.checklistMonth()));
    let paidBills: Record<string, boolean> = {};
    try {
      paidBills = saved ? JSON.parse(saved) as Record<string, boolean> : {};
    } catch {
      paidBills = {};
    }
    this.monthlyBills.set(this.defaultBills().map((name) => ({ name, paid: paidBills[name] === true })));
  }

  private checklistStorageKey(userId: number, month: string): string {
    return `billing-checklist:${userId}:${month}`;
  }

  private currentMonth(): string {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  }

  private defaultBills(): string[] {
    return ['Condomínio', 'Carro', 'Luz', 'Gás', 'Internet', 'IPTU', 'Estacionamento'];
  }

  protected categoryChartStyle(): string {
    const categories = this.summary().categorias;
    const total = this.categoryDistributionTotal();
    if (!total) return 'conic-gradient(#30423b 0 100%)';
    let start = 0;
    const categoriesWithAmounts = categories
      .filter((category) => Number.isFinite(Number(category.total)) && Number(category.total) > 0);
    const segments = categoriesWithAmounts.map((category, segmentIndex) => {
      const amount = Number(category.total);
      const end = start + (amount / total) * 100;
      const segmentEnd = segmentIndex === categoriesWithAmounts.length - 1 ? 100 : end;
      const segment = `${this.categoryColor(category.nome)} ${start}% ${segmentEnd}%`;
      start = end;
      return segment;
    });
    if (!segments.length) return 'conic-gradient(#30423b 0 100%)';
    return `conic-gradient(${segments.join(', ')})`;
  }

  protected categoryColor(categoryName: string): string {
    const assignedColor = this.assignedCategoryColors.get(categoryName);
    if (assignedColor) return assignedColor;

    const fixedColor = FIXED_CATEGORY_COLORS.get(categoryName);
    const fixedHue = FIXED_CATEGORY_HUES.get(categoryName);
    if (fixedColor && fixedHue !== undefined) {
      this.assignedCategoryColors.set(categoryName, fixedColor);
      this.assignedCategoryHues.set(categoryName, fixedHue);
      return fixedColor;
    }

    let hash = 2166136261;
    for (const character of categoryName) {
      hash = Math.imul(hash ^ character.charCodeAt(0), 16777619) >>> 0;
    }

    const initialHue = hash % 360;
    const usedHues = [...FIXED_CATEGORY_HUES.values(), ...this.assignedCategoryHues.values()];
    let selectedHue = initialHue;
    let widestGap = -1;
    for (let attempt = 0; attempt < 360; attempt += 1) {
      const hue = (initialHue + attempt * 137.508) % 360;
      const nearestHue = Math.min(
        ...usedHues.map((usedHue) => {
          const distance = Math.abs(hue - usedHue);
          return Math.min(distance, 360 - distance);
        }),
      );
      if (nearestHue > widestGap) {
        selectedHue = hue;
        widestGap = nearestHue;
      }
      if (nearestHue >= 26) {
        selectedHue = hue;
        break;
      }
    }

    const color = `hsl(${Math.round(selectedHue)} 62% 56%)`;
    this.assignedCategoryColors.set(categoryName, color);
    this.assignedCategoryHues.set(categoryName, selectedHue);
    return color;
  }

  protected categoryDistributionTotal(): number {
    return this.summary().categorias.reduce((sum, category) => {
      const total = Number(category.total);
      return sum + (Number.isFinite(total) && total > 0 ? total : 0);
    }, 0);
  }

  protected categoryDistributionPercent(category: { total: number }): number {
    const total = Number(category.total);
    const distributionTotal = this.categoryDistributionTotal();
    return distributionTotal && Number.isFinite(total) && total > 0
      ? Math.min((total / distributionTotal) * 100, 100)
      : 0;
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

  protected loadCategories(): void {
    this.http.get<Category[]>('/api/categorias').subscribe({
      next: (categories) => this.categories.set(categories),
      error: () => this.error.set('Não foi possível carregar as categorias.'),
    });
  }

  protected addCategory(): void {
    const name = this.newCategory.trim();
    if (!name) {
      this.error.set('Informe o nome da categoria.');
      return;
    }
    this.http.post<Category>('/api/categorias', { nome: name }).subscribe({
      next: (category) => {
        this.categories.update((categories) => [...categories, category].sort((a, b) => a.nome.localeCompare(b.nome)));
        this.manual.categoria = category.nome;
        this.newCategory = '';
        this.message.set('Categoria criada.');
        this.error.set('');
        this.loadDashboard();
      },
      error: (response) => this.error.set(response.error?.detail ?? 'Não foi possível criar a categoria.'),
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
    if (this.searchTerm.trim()) params = params.set('busca', this.searchTerm.trim());
    return params;
  }

  protected applySearch(): void {
    this.loadDashboard();
  }

  protected deleteAllTransactions(): void {
    const confirmed = window.confirm('Excluir todos os lançamentos? Esta ação não pode ser desfeita.');
    if (!confirmed) return;
    this.http.delete<{ message: string }>('/api/transacoes').subscribe({
      next: (response) => {
        this.message.set(response.message);
        this.error.set('');
        this.loadDashboard();
      },
      error: () => this.error.set('Não foi possível excluir os lançamentos.'),
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
    this.uploadFile(file, input);
  }

  protected onFileDragOver(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.draggingFile.set(true);
  }

  protected onFileDragLeave(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.draggingFile.set(false);
  }

  protected onFileDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.draggingFile.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) this.uploadFile(file);
  }

  private uploadFile(file: File, input?: HTMLInputElement): void {
    if (!file.name.toLowerCase().match(/\.(pdf|txt|csv)$/)) {
      this.error.set('Envie um arquivo PDF, TXT ou CSV.');
      if (input) input.value = '';
      return;
    }
    const formData = new FormData();
    formData.append('file', file);
    this.http.post<{ message: string }>('/api/importar', formData).subscribe({
      next: (response) => {
        const filtersAreActive = this.dataInicio || this.dataFim || this.searchTerm.trim();
        this.message.set(
          filtersAreActive
            ? `${response.message} Os filtros atuais continuam aplicados; lançamentos fora deles não aparecem na lista.`
            : response.message,
        );
        this.error.set('');
        if (input) input.value = '';
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
    this.http.patch<{ message: string }>(`/api/transacoes/${transaction.id}`, this.editDraft).subscribe({
      next: (response) => {
        this.message.set(response.message);
        this.error.set('');
        this.editingId.set(null);
        this.loadDashboard();
      },
      error: () => this.error.set('Não foi possível atualizar o lançamento.'),
    });
  }

  protected deleteTransaction(transaction: Transaction): void {
    const confirmed = window.confirm(`Excluir o lançamento "${transaction.descricao}"?`);
    if (!confirmed) return;
    this.http.delete<{ message: string }>(`/api/transacoes/${transaction.id}`).subscribe({
      next: (response) => {
        this.message.set(response.message);
        this.error.set('');
        this.loadDashboard();
      },
      error: () => this.error.set('Não foi possível excluir o lançamento.'),
    });
  }

  protected formatCurrency(value: number): string {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);
  }

  protected formatDate(value: string): string {
    return new Intl.DateTimeFormat('pt-BR').format(new Date(`${value}T12:00:00`));
  }
}
