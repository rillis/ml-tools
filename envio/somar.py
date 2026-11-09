"""Soma os SKUs de um PDF de envio do Full (instruções de preparação) da pasta envio/input/.

Uso (no servidor, na raiz do projeto):  .venv/bin/python -m envio [ARQUIVO.pdf]

Sem ARQUIVO, mostra os 3 PDFs mais recentes de envio/input/ para escolher um.
Separa os kits ("KF-216+KA-035", "3xKA-250") em peças, junta os SKUs iguais (ignora maiúsculas)
e imprime um por linha, como "11x KF-216", em ordem alfabética.
Confere o número de produtos e de unidades com o cabeçalho do PDF; se não bater, mostra o erro.
O PDF não é apagado.
"""
import argparse
import logging
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import questionary
from questionary import Choice
from pypdf import PdfReader

ENTRADA = Path(__file__).resolve().parent / 'input'
logging.getLogger('pypdf').setLevel(logging.ERROR)  # avisos de leitura do pypdf poluem a lista


def ler(pdf):
    """(frete, [(sku, unidades)]) do PDF; ValueError se a leitura não bater com o cabeçalho."""
    paginas = [p.extract_text() or '' for p in PdfReader(pdf).pages]
    texto = '\n'.join(paginas)
    cab = re.search(r'Produtos do envio:\s*(\d+)\s*\|\s*Total de unidades:\s*(\d+)', texto)
    frete = re.search(r'Frete\s*#\s*(\d+)', texto)
    if not cab:
        raise ValueError('não achei "Produtos do envio" / "Total de unidades" (é um PDF de envio do Full?)')
    linhas = []
    for n, pag in enumerate(paginas, 1):
        skus = re.findall(r'SKU:\s*(\S+)', pag)
        partes = re.split(r'PRODUTO\s+UNIDADES[^\n]*\n', pag)
        qtds = [int(q) for q in re.findall(r'^\s*(\d+)\s*$', partes[-1], re.M)] if len(partes) > 1 else []
        if len(skus) != len(qtds):
            raise ValueError(f'página {n}: {len(skus)} SKUs e {len(qtds)} quantidades')
        linhas += zip(skus, qtds)
    produtos, unidades = int(cab.group(1)), int(cab.group(2))
    if len(linhas) != produtos or sum(q for _, q in linhas) != unidades:
        raise ValueError(f'li {len(linhas)} produtos e {sum(q for _, q in linhas)} unidades, '
                         f'mas o PDF diz {produtos} e {unidades}')
    return (frete.group(1) if frete else pdf.stem), linhas


def somar(linhas):
    """Separa os kits e soma por SKU: 6x "KF-216+KA-035" = 6x KF-216 + 6x KA-035."""
    total = Counter()
    for sku, qtd in linhas:
        for parte in (p.strip() for p in sku.split('+') if p.strip()):
            m = re.match(r'^(\d+)\s*[xX]\s*(.+)$', parte)
            n, nome = (int(m.group(1)), m.group(2)) if m else (1, parte)
            total[nome.strip().upper()] += n * qtd
    return total


def escolher():
    """Um dos 3 PDFs mais recentes de input/ (ou None se cancelar)."""
    ENTRADA.mkdir(exist_ok=True)
    pdfs = sorted((p for p in ENTRADA.iterdir() if p.suffix.lower() == '.pdf'),
                  key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    if not pdfs:
        sys.exit(f'Nenhum PDF em {ENTRADA}')
    return questionary.select('Qual PDF de envio?', choices=[
        Choice(f'{p.name}  ({datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M})', p) for p in pdfs]).ask()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('arquivo', nargs='?', help='PDF de envio (padrão: escolher entre os 3 mais recentes de input/)')
    args = ap.parse_args()

    pdf = Path(args.arquivo) if args.arquivo else escolher()
    if pdf is None:
        sys.exit(130)
    try:
        _, linhas = ler(pdf)
    except Exception as e:  # PDF ilegível ou fora do formato
        sys.exit(f'{pdf.name}: {e}')
    total = somar(linhas)
    print('\n'.join(f'{total[s]}x {s}' for s in sorted(total)))


if __name__ == '__main__':
    main()
