# -*- coding: utf-8 -*-
"""Canal de extração — a metade CENTRAL do canal de entrega dos nós.

O nó processa o .ufdr na SUA CPU e não tem banco nenhum. Cada escritor de domínio da
pipeline, em vez do INSERT, EMITE um registro JSON com o mesmo dict natural que receberia
(conversas, mensagens, contatos... — nenhum id gerado no banco atravessa o canal). Os
registros viram LOTES JSONL no disco do nó, que sobem ao central em sequência; o central
REPRÓDUZ cada registro chamando a MESMA função de gravação local (REPLAY abaixo) — mesma
SQL, mesmo ON CONFLICT, mesmo dedup. O `id_extracao` é criado no central e usado igual nos
dois lados, então não há remapeamento.

O EMISSOR (CanalEntrega/spool/entrega) roda no nó — e o código do nó vive em repositório
próprio. Aqui ficam o RECEBEDOR (`reproduzir_lote`, `aplicar_estado`) e os guards inertes
(`emitir`/`estado_agora`), que no central nunca encontram canal ativo: os escritores de
`investigacao_utils` gravam direto no banco, como sempre.
"""
import base64
import contextvars
import datetime
import decimal
import json
import logging

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- emissor

_EMISSOR: contextvars.ContextVar[object | None] = contextvars.ContextVar("canal_emissor", default=None)


def emissor_ativo():
    return _EMISSOR.get()


def ativar(canal):
    """Para threads de worker dedicadas (a pipeline roda em thread própria): ativa o canal
    e devolve o token para `desativar` no finally."""
    return _EMISSOR.set(canal)


def desativar(token) -> None:
    _EMISSOR.reset(token)


def emitir(tipo: str, args: tuple) -> bool:
    """Chamado no topo de cada escritor de domínio: devolve True se o canal absorveu a
    escrita (modo entrega — o chamador deve voltar sem tocar no banco)."""
    canal = _EMISSOR.get()
    if canal is None:
        return False
    canal.emitir(tipo, args)
    return True


def em_modo_entrega() -> bool:
    return _EMISSOR.get() is not None


def estado_agora(payload: dict) -> bool:
    """Estado (fase/contagens/hashes/...) do worker para o central, agora. Devolve True se
    o canal absorveu — as funções de estado da pipeline usam isso para pular o UPDATE."""
    canal = _EMISSOR.get()
    if canal is None:
        return False
    canal.estado(payload)
    return True


# ----------------------------------------------------------------------------- JSON seguro

def _dec(o):
    if isinstance(o, dict) and len(o) == 1:
        if "__dt__" in o:
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.datetime.fromisoformat(o["__dt__"])
                except ValueError:
                    continue
            return o["__dt__"]
        if "__dec__" in o:
            return decimal.Decimal(o["__dec__"])
        if "__b64__" in o:
            return base64.b64decode(o["__b64__"])
        if "__set__" in o:
            return set(o["__set__"])
    if isinstance(o, dict):
        return {k: _dec(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_dec(v) for v in o]
    return o


# ----------------------------------------------------------------------------- replay (central)

def _fu():
    from funcoes import investigacao_utils
    return investigacao_utils


def _replay_args(args: list):
    return tuple(_dec(a) for a in args)


def reproduzir_lote(cur, linhas_texto: str, id_extracao: int) -> int:
    """Reproduz um lote JSONL no banco do central chamando as MESMAS funções de gravação
    da pipeline. Cada registro carrega só dados naturais + o id_extracao global."""
    IU = _fu()
    gravados = 0
    # splitlines() é guloso: também corta em \x85 (NEL), \u2028 e \u2029 — que o json.dumps
    # (ensure_ascii=False, no emissor) deixa CRUS dentro das strings. Um só U+2028 numa
    # mensagem fendia um registro ao meio e o central rejeitava o lote inteiro
    # (422/quarentena, extração em 'erro' — visto no nó SMT, lote 94, linha 167:
    # 'Unterminated string'). O separador de recorde é só \n (o emissor junta por \n e o
    # json.dumps escapa todo \n interno), então fender por \n é o correto.
    for n_linha, linha in enumerate(linhas_texto.split("\n"), start=1):
        if not linha.strip():
            continue
        # ValueError (e não JSONDecodeError cru): o roteador devolve 422 e o nó QUARENTENA
        # o lote em vez de reenviar o mesmo veneno para sempre (deadlock de entrega)
        try:
            registro = json.loads(linha)
        except ValueError as exc:
            raise ValueError(f"linha {n_linha} do lote não é JSON válido: {exc}") from exc
        if not isinstance(registro, dict) or "t" not in registro:
            raise ValueError(f"linha {n_linha} do lote sem tipo de registro")
        tipo, args = registro["t"], _replay_args(registro["a"])
        if tipo == "conversa":
            IU._gravar_conversa(cur, *args)
        elif tipo == "conversa_msg":
            IU._gravar_conversa_msg(cur, *args)
        elif tipo == "contato_agenda":
            IU._gravar_contato_agenda(cur, *args)
        elif tipo == "ligacao":
            IU._gravar_ligacao(cur, *args)
        elif tipo == "localizacao":
            IU._gravar_localizacao(cur, *args)
        elif tipo == "notificacao":
            IU._gravar_notificacao(cur, *args)
        elif tipo == "nota":
            IU._gravar_nota(cur, *args)
        elif tipo == "pagina_visitada":
            IU._gravar_pagina_visitada(cur, *args)
        elif tipo == "aplicativo":
            IU._gravar_aplicativo(cur, *args)
        elif tipo == "dono":
            IU._gravar_dono(cur, *args)
        elif tipo == "conta_usuario":
            IU._gravar_conta_usuario(cur, *args)
        elif tipo == "email":
            IU._gravar_email(cur, *args)
        elif tipo == "conta_financeira":
            IU._gravar_conta_financeira(cur, *args)
        elif tipo == "senha":
            IU._gravar_senha(cur, *args)
        elif tipo == "autofill":
            IU._gravar_autofill(cur, *args)
        elif tipo == "rede_wifi":
            IU._gravar_rede_wifi(cur, *args)
        elif tipo == "dominio_novo":
            IU._gravar_dominio_novo(cur, *args)
        elif tipo == "arquivos":
            IU._drenar_arquivos(cur, [tuple(l) for l in args[0]])
        elif tipo == "acesso_conta":
            from funcoes.contas_nuvem_utils import _gravar_acesso_conta
            _gravar_acesso_conta(cur, *args)
        elif tipo == "atividade_conta":
            from funcoes.contas_nuvem_utils import _gravar_atividade_conta
            _gravar_atividade_conta(cur, *args)
        elif tipo == "evento_conta":
            from funcoes.contas_nuvem_utils import _gravar_evento_conta
            _gravar_evento_conta(cur, *args)
        elif tipo == "perfil_conta":
            from funcoes.contas_nuvem_utils import _gravar_perfil_conta
            _gravar_perfil_conta(cur, *args)
        elif tipo == "dispositivo_conta":
            from funcoes.contas_nuvem_utils import _gravar_dispositivo_conta
            _gravar_dispositivo_conta(cur, *args)
        elif tipo == "metadado_nuvem":
            from funcoes.nuvem_metadados import gravar_metadado
            gravar_metadado(cur, *args)
        elif tipo == "dono_ios":
            IU._derivar_dono_ios(cur, *args)
        elif tipo == "resolver_anexos":
            IU._resolver_anexos_por_file_id(cur, *args)
        elif tipo == "resolver_localizacoes":
            IU._resolver_localizacoes_compartilhadas(cur, *args)
        elif tipo == "resolver_contatos_chatstorage":
            IU._resolver_contatos_chatstorage(cur, *args)
        elif tipo == "conta_apps_ios":
            IU._gravar_conta_apps_ios(cur, *args)
        elif tipo == "corrigir_direcao":
            # adiada: precisa rodar com TUDO commitado (abre conexão própria) — o handler
            # da entrega a executa uma vez no finalize, junto com PIX/identificação.
            pass
        elif tipo == "transcricao":
            IU._aplicar_transcricao(cur, *args)
        else:
            raise ValueError(f"registro de canal desconhecido: {tipo!r}")
        gravados += 1
    return gravados


# ----------------------------------------------------------------------------- estado (central)

# colunas de estado que o nó pode atualizar na linha da extração (whitelist estrita)
_CAMPOS_DIRETOS = ("cd_fase", "qt_progresso_fase", "no_dispositivo", "in_nuvem",
                   "co_hash_md5", "co_hash_sha1", "co_hash_sha256")


def aplicar_estado(id_extracao: int, payload: dict) -> dict:
    """Aplica um callback de estado do nó na INQ_TB02_EXTRACOES. Responde {cancelar}."""
    from funcoes.db_utils import get_connection

    t = payload.get("t")
    # TODOS os UPDATEs filtram cd_status = 'processando': um callback atrasado/reenviado
    # (rede instável) nunca pode puxar uma extração já 'concluido' de volta para fase/erro
    # algum. O guard do 'erro' já existia; agora são todos.
    with get_connection() as conn, conn.cursor() as cur:
        if t in ("fase", "progresso", "batida"):
            campos = {"dt_ultima_atividade": "now()"}
            params: list = []
            for campo in ("cd_fase", "qt_progresso_fase"):
                if campo in payload:
                    campos[campo] = "%s"
                    params.append(payload[campo])
            cur.execute(f"UPDATE investigacao.inq_tb02_extracoes SET {', '.join(f'{c} = {v}' for c, v in campos.items())} "
                        f"WHERE id_extracao = %s AND cd_status = 'processando'",
                        (*params, id_extracao))
        elif t == "hashes":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET co_hash_md5 = %s, co_hash_sha1 = %s, "
                        "co_hash_sha256 = %s, dt_ultima_atividade = now() "
                        "WHERE id_extracao = %s AND cd_status = 'processando'",
                        (payload.get("md5"), payload.get("sha1"), payload.get("sha256"), id_extracao))
        elif t == "info_dispositivo":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET no_dispositivo = %s, no_fabricante = %s, "
                        "dt_extracao = %s, in_nuvem = %s, dt_ultima_atividade = now() "
                        "WHERE id_extracao = %s AND cd_status = 'processando'",
                        (payload.get("no_dispositivo"), payload.get("no_fabricante"),
                         payload.get("dt_extracao"), bool(payload.get("in_nuvem")), id_extracao))
        elif t == "arquivo_organizado":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET no_arquivo = %s, "
                        "dt_ultima_atividade = now() WHERE id_extracao = %s AND cd_status = 'processando'",
                        (str(payload.get("no_arquivo"))[:1000], id_extracao))
        elif t == "contas_total":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET qt_contas_financeiras = %s, "
                        "dt_ultima_atividade = now() WHERE id_extracao = %s AND cd_status = 'processando'",
                        (int(payload.get("total") or 0), id_extracao))
        elif t == "contagens":
            IU = _fu()
            IU._gravar_contagens_extracao(id_extracao, payload.get("contagens") or {})
        elif t == "erro":
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET cd_status = 'erro', cd_fase = NULL, "
                        "qt_progresso_fase = NULL, tx_erro = %s, dt_fim_processamento = now() "
                        "WHERE id_extracao = %s AND cd_status = 'processando'",
                        (str(payload.get("mensagem"))[:2000], id_extracao))
        elif t == "transcricao":
            # texto de um áudio transcrito na estação (botão "Transcrever" rodando no nó):
            # grava nos anexos desta extração cujo binário é o caminho_zip (mesma função do replay)
            IU = _fu()
            IU._aplicar_transcricao(cur, payload.get("prefixo"), id_extracao,
                                    payload.get("caminho_zip"), payload.get("texto"))
        elif t == "transcricao_fim":
            # fecho da transcrição remota: devolve a extração a 'concluido' somando os transcritos
            # da rodada (mesmo UPDATE do worker local que não existe mais no central)
            cur.execute("UPDATE investigacao.inq_tb02_extracoes SET cd_status = 'concluido', "
                        "cd_fase = NULL, qt_progresso_fase = NULL, dt_fim_processamento = now(), "
                        "tx_aviso = %s, qt_audios_transcritos = coalesce(qt_audios_transcritos, 0) + %s "
                        "WHERE id_extracao = %s AND cd_status = 'processando'",
                        ((str(payload.get("aviso"))[:2000] if payload.get("aviso") else None),
                         int(payload.get("qt") or 0), id_extracao))
        conn.commit()
        cur.execute("SELECT fl_cancelar FROM investigacao.inq_tb02_extracoes WHERE id_extracao = %s",
                    (id_extracao,))
        linha = cur.fetchone()
    return {"cancelar": bool(linha and linha[0])}
