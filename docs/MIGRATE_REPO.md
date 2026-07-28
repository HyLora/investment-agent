# Come spostare InvestmentAgent su una repo dedicata

Questo workspace cloud era collegato a `HyLora/expense-agent`.
`InvestmentAgent` deve vivere in **`HyLora/investment-agent`**.

L'integrazione GitHub di Cursor non può creare repository (`createRepository` negato).

## Passi (da fare sul tuo account GitHub)

1. Crea un repository **vuoto** `investment-agent` sotto `HyLora` (senza README/licenza).
2. Dal clone di questo progetto:

```bash
git remote remove origin
git remote add origin https://github.com/HyLora/investment-agent.git
git branch -M main
git push -u origin main
```

Se preferisci conservare lo storico del branch feature:

```bash
git push -u origin cursor/investment-agent-architecture-678b:main
```

3. Aggiorna il progetto Cursor / cloud agent affinché punti a `HyLora/investment-agent` nelle run successive.
