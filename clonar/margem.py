"""Depois de clonar: busca de novo o anúncio novo e o original na última busca completa e recalcula a margem
(o mesmo que o "Só alguns"). Sem busca completa terminada, só avisa.

Uso:  .venv/bin/python -m clonar.margem
"""
import sys

from clonar import ULTIMO
from comum import ml
from margem import coleta, custo


def main():
    if not ULTIMO.exists():
        print('Nenhum anúncio novo para atualizar na margem.')
        return
    ids = [i for i in ULTIMO.read_text(encoding='utf-8').strip().split(',') if i]
    db = coleta.DADOS / 'margem.db'
    if not db.exists() or coleta.meta(coleta.abrir_db(db), 'concluida') != '1':
        print('Sem uma busca completa terminada: os anúncios entram na margem no próximo Atualizar margens.')
        return
    trava = ml.travar_busca()  # noqa: F841
    coleta.setup_log()
    coleta.atualizar_alguns(ids)
    sys.argv = [sys.argv[0]]
    custo.main()


if __name__ == '__main__':
    main()
