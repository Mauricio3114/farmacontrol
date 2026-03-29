import os
import secrets
import json
import io
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

import requests
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, jsonify, session, send_file, abort
)
from flask_login import (
    login_user, login_required, logout_user,
    current_user
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from pywebpush import webpush, WebPushException

from extensions import db, login_manager

from models import (
    Farmacia, User, Cliente, Entregador, Pedido, Localizacao,
    WhatsAppConfig, WhatsAppLog, UsuarioFarmacia, EntregadorFarmacia,
    EntregadorPushSubscription
)


# =========================
# WHATSAPP (ENVIO TEMPLATE)
# =========================
def enviar_whatsapp_template(telefone, template_nome, parametros):
    config = WhatsAppConfig.query.first()

    if not config or not config.ativo:
        return

    if not config.access_token or not config.phone_number_id:
        return

    url = f"https://graph.facebook.com/v22.0/{config.phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "template",
        "template": {
            "name": template_nome,
            "language": {
                "code": "pt_BR"
            },
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(p)} for p in parametros
                    ]
                }
            ]
        }
    }

    try:
        requests.post(url, headers=headers, json=data, timeout=20)
    except Exception:
        pass


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

DB_PATH = os.path.join(INSTANCE_DIR, "farmacia.db")


def agora_brasil():
    try:
        return datetime.now(ZoneInfo("America/Fortaleza"))
    except Exception:
        return datetime.now()


def gerar_codigo_rastreio():
    return secrets.token_urlsafe(8).replace("-", "").replace("_", "")


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "super-secret-key")

    database_url = os.environ.get("DATABASE_URL")
    em_render = os.environ.get("RENDER") == "true"

    if not database_url:
        if em_render:
            raise RuntimeError("DATABASE_URL não configurada em produção.")
        database_url = f"sqlite:///{DB_PATH}"

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # sessão do entregador fica salva por mais tempo
    app.config["SESSION_PERMANENT"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"

    with app.app_context():
        db.create_all()
        criar_admin_master_padrao()
        migrar_usuarios_legados_para_vinculos()
        migrar_entregadores_legados_para_vinculos()
        garantir_codigo_rastreio_nos_pedidos()

    registrar_rotas(app)

    return app
   


def criar_admin_master_padrao():
    admin = User.query.filter_by(email="admin@farmacontrol.com").first()

    if not admin:
        admin = User(
            nome="Administrador Master",
            email="admin@farmacontrol.com",
            perfil="master",
            farmacia_id=None,
            ativo=True
        )
        admin.set_password("123456")
        db.session.add(admin)
        db.session.commit()


def migrar_entregadores_legados_para_vinculos():
    entregadores = Entregador.query.all()
    alterou = False

    for entregador in entregadores:
        if entregador.farmacia_id:
            existe = EntregadorFarmacia.query.filter_by(
                entregador_id=entregador.id,
                farmacia_id=entregador.farmacia_id
            ).first()

            if not existe:
                vinculo = EntregadorFarmacia(
                    entregador_id=entregador.id,
                    farmacia_id=entregador.farmacia_id,
                    ativo=True
                )
                db.session.add(vinculo)
                alterou = True

    if alterou:
        db.session.commit()


def migrar_usuarios_legados_para_vinculos():
    """
    Cria vínculos em usuarios_farmacias para usuários antigos que ainda só têm farmacia_id.
    """
    usuarios = User.query.filter(User.perfil != "master").all()
    alterou = False

    for usuario in usuarios:
        if usuario.farmacia_id:
            existe = UsuarioFarmacia.query.filter_by(
                usuario_id=usuario.id,
                farmacia_id=usuario.farmacia_id
            ).first()

            if not existe:
                vinculo = UsuarioFarmacia(
                    usuario_id=usuario.id,
                    farmacia_id=usuario.farmacia_id,
                    perfil="admin",
                    ativo=True
                )
                db.session.add(vinculo)
                alterou = True

    if alterou:
        db.session.commit()


def migrar_entregadores_legados_para_vinculos():
    entregadores = Entregador.query.all()
    alterou = False

    for entregador in entregadores:
        if entregador.farmacia_id:
            existe = EntregadorFarmacia.query.filter_by(
                entregador_id=entregador.id,
                farmacia_id=entregador.farmacia_id
            ).first()

            if not existe:
                vinculo = EntregadorFarmacia(
                    entregador_id=entregador.id,
                    farmacia_id=entregador.farmacia_id,
                    ativo=True
                )
                db.session.add(vinculo)
                alterou = True

    if alterou:
        db.session.commit()

def garantir_codigo_rastreio_nos_pedidos():
    pedidos = Pedido.query.filter(
        (Pedido.codigo_rastreio.is_(None)) | (Pedido.codigo_rastreio == "")
    ).all()

    alterou = False

    for pedido in pedidos:
        codigo = gerar_codigo_rastreio()
        while Pedido.query.filter_by(codigo_rastreio=codigo).first():
            codigo = gerar_codigo_rastreio()

        pedido.codigo_rastreio = codigo
        alterou = True

    if alterou:
        db.session.commit()


def garantir_whatsapp_config(farmacia_id):
    cfg = WhatsAppConfig.query.filter_by(farmacia_id=farmacia_id).first()
    if not cfg:
        cfg = WhatsAppConfig(
            farmacia_id=farmacia_id,
            ativo=False,
            verify_token=f"pharmaflow_verify_token_{farmacia_id}",
            nome_template_pedido_recebido="pedido_recebido",
            nome_template_saiu_entrega="pedido_saiu_entrega",
            nome_template_pedido_entregue="pedido_entregue",
            enviar_pedido_recebido=True,
            enviar_saiu_entrega=True,
            enviar_pedido_entregue=True
        )
        db.session.add(cfg)
        db.session.commit()


def master_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        if not current_user.is_master:
            flash("Acesso permitido apenas para o administrador master.", "danger")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)
    return wrapper


def farmacias_ids_do_usuario():
    if not current_user.is_authenticated:
        return []

    if current_user.is_master:
        return [
            f.id for f in Farmacia.query.filter_by(ativo=True, status="ativa").all()
        ]

    ids = list(current_user.farmacias_ids or [])

    if not ids and current_user.farmacia_id:
        ids = [current_user.farmacia_id]

    if not ids:
        return []

    farmacias_validas = Farmacia.query.filter(
        Farmacia.id.in_(ids),
        Farmacia.ativo.is_(True),
        Farmacia.status == "ativa"
    ).all()

    return [f.id for f in farmacias_validas]


def farmacias_do_usuario_logado():
    ids = farmacias_ids_do_usuario()
    if not ids:
        return []

    return Farmacia.query.filter(Farmacia.id.in_(ids)).order_by(Farmacia.nome.asc()).all()


def farmacia_ativa_id():
    if not current_user.is_authenticated or current_user.is_master:
        return None

    ids = farmacias_ids_do_usuario()
    if not ids:
        return None

    farmacia_id_sessao = session.get("farmacia_ativa_id")
    if farmacia_id_sessao in ids:
        return farmacia_id_sessao

    session["farmacia_ativa_id"] = ids[0]
    return ids[0]


def definir_farmacia_ativa(farmacia_id):
    if not current_user.is_authenticated or current_user.is_master:
        return False

    ids = farmacias_ids_do_usuario()
    if farmacia_id in ids:
        session["farmacia_ativa_id"] = farmacia_id
        return True
    return False


def user_farmacia_id():
    if current_user.is_master:
        return None
    return farmacia_ativa_id()


def validar_acesso_farmacia(farmacia_id):
    if current_user.is_master:
        return True
    return current_user.possui_acesso_farmacia(farmacia_id)


def cliente_query():
    if current_user.is_master:
        return Cliente.query

    farmacia_id = farmacia_ativa_id()
    if not farmacia_id:
        return Cliente.query.filter(Cliente.id == 0)

    return Cliente.query.filter_by(farmacia_id=farmacia_id)


def entregador_query():
    if current_user.is_master:
        return Entregador.query

    farmacia_id = farmacia_ativa_id()
    if not farmacia_id:
        return Entregador.query.filter(Entregador.id == 0)

    return Entregador.query.filter_by(farmacia_id=farmacia_id)


def pedido_query():
    if current_user.is_master:
        return Pedido.query

    ids = farmacias_ids_do_usuario()
    if not ids:
        return Pedido.query.filter(Pedido.id == 0)

    return Pedido.query.filter(Pedido.farmacia_id.in_(ids))


def farmacia_do_usuario_logado():
    if current_user.is_master:
        return None

    farmacia_id = farmacia_ativa_id()
    if not farmacia_id:
        return None

    return db.session.get(Farmacia, farmacia_id)


def link_google_maps(endereco: str) -> str:
    destino = quote_plus(endereco or "")
    return f"https://www.google.com/maps/dir/?api=1&destination={destino}"


def link_waze(endereco: str) -> str:
    destino = quote_plus(endereco or "")
    return f"https://waze.com/ul?q={destino}&navigate=yes"


def normalizar_telefone_br(numero: str) -> str:
    numero_limpo = "".join(ch for ch in (numero or "") if ch.isdigit())

    if not numero_limpo:
        return ""

    if numero_limpo.startswith("55"):
        return f"+{numero_limpo}"

    return f"+55{numero_limpo}"


def link_whatsapp(numero: str, mensagem: str) -> str:
    numero_limpo = "".join(ch for ch in (numero or "") if ch.isdigit())
    texto = quote_plus(mensagem)
    return f"https://wa.me/{numero_limpo}?text={texto}"


def numero_whatsapp_formatado(numero: str) -> str:
    numero_normalizado = normalizar_telefone_br(numero)
    return "".join(ch for ch in numero_normalizado if ch.isdigit())


def criar_log_whatsapp(
    tipo,
    farmacia_id=None,
    destino=None,
    mensagem=None,
    template_nome=None,
    pedido_id=None,
    status="pendente",
    resposta_api=None,
    direction="outbound"
):
    log = WhatsAppLog(
        farmacia_id=farmacia_id,
        tipo=tipo,
        destino=destino,
        mensagem=mensagem,
        template_nome=template_nome,
        pedido_id=pedido_id,
        status=status,
        resposta_api=resposta_api,
        direction=direction
    )
    db.session.add(log)
    db.session.commit()
    return log


def obter_config_whatsapp(farmacia_id):
    if not farmacia_id:
        return None
    return WhatsAppConfig.query.filter_by(farmacia_id=farmacia_id).first()


def enviar_template_whatsapp(
    numero,
    farmacia_id,
    template_nome,
    pedido_id=None,
    tipo="template",
    components=None
):
    cfg = obter_config_whatsapp(farmacia_id)

    if not cfg or not cfg.ativo:
        return {
            "ok": False,
            "mensagem": "WhatsApp não está ativo nas configurações."
        }

    if not cfg.access_token or not cfg.phone_number_id:
        return {
            "ok": False,
            "mensagem": "Credenciais do WhatsApp incompletas."
        }

    if not template_nome:
        return {
            "ok": False,
            "mensagem": "Nome do template não informado."
        }

    numero_limpo = numero_whatsapp_formatado(numero)

    payload = {
        "messaging_product": "whatsapp",
        "to": numero_limpo,
        "type": "template",
        "template": {
            "name": template_nome,
            "language": {
                "code": "pt_BR"
            }
        }
    }

    if components:
        payload["template"]["components"] = components

    url = f"https://graph.facebook.com/v23.0/{cfg.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {cfg.access_token}",
        "Content-Type": "application/json"
    }

    log = criar_log_whatsapp(
        tipo=tipo,
        farmacia_id=farmacia_id,
        destino=numero_limpo,
        template_nome=template_nome,
        pedido_id=pedido_id,
        status="enviando"
    )

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        resposta_texto = resp.text

        if resp.ok:
            log.status = "enviado"
            log.resposta_api = resposta_texto
            db.session.commit()
            return {
                "ok": True,
                "mensagem": "Template enviado com sucesso.",
                "resposta": resposta_texto
            }

        log.status = "erro"
        log.resposta_api = resposta_texto
        db.session.commit()
        return {
            "ok": False,
            "mensagem": "Erro ao enviar template.",
            "resposta": resposta_texto
        }

    except Exception as e:
        log.status = "erro"
        log.resposta_api = str(e)
        db.session.commit()
        return {
            "ok": False,
            "mensagem": f"Falha na conexão com WhatsApp: {str(e)}"
        }


def enviar_texto_whatsapp(numero, mensagem, farmacia_id, pedido_id=None, tipo="texto_manual"):
    cfg = obter_config_whatsapp(farmacia_id)

    if not cfg or not cfg.ativo:
        return {
            "ok": False,
            "mensagem": "WhatsApp não está ativo nas configurações."
        }

    if not cfg.access_token or not cfg.phone_number_id:
        return {
            "ok": False,
            "mensagem": "Credenciais do WhatsApp incompletas."
        }

    numero_limpo = numero_whatsapp_formatado(numero)

    payload = {
        "messaging_product": "whatsapp",
        "to": numero_limpo,
        "type": "text",
        "text": {
            "body": mensagem
        }
    }

    url = f"https://graph.facebook.com/v23.0/{cfg.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {cfg.access_token}",
        "Content-Type": "application/json"
    }

    log = criar_log_whatsapp(
        tipo=tipo,
        farmacia_id=farmacia_id,
        destino=numero_limpo,
        mensagem=mensagem,
        pedido_id=pedido_id,
        status="enviando"
    )

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        resposta_texto = resp.text

        if resp.ok:
            log.status = "enviado"
            log.resposta_api = resposta_texto
            db.session.commit()
            return {
                "ok": True,
                "mensagem": "Mensagem enviada com sucesso.",
                "resposta": resposta_texto
            }

        log.status = "erro"
        log.resposta_api = resposta_texto
        db.session.commit()
        return {
            "ok": False,
            "mensagem": "Erro ao enviar mensagem.",
            "resposta": resposta_texto
        }

    except Exception as e:
        log.status = "erro"
        log.resposta_api = str(e)
        db.session.commit()
        return {
            "ok": False,
            "mensagem": f"Falha na conexão com WhatsApp: {str(e)}"
        }


def disparar_whatsapp_pedido_recebido(pedido):
    cfg = obter_config_whatsapp(pedido.farmacia_id)
    if not cfg or not cfg.ativo or not cfg.enviar_pedido_recebido:
        return

    enviar_whatsapp_template(
        pedido.cliente.telefone,
        "pedido_recebido",
        [
            pedido.cliente.nome,
            pedido.id
        ]
    )


def disparar_whatsapp_saiu_entrega(pedido):
    cfg = obter_config_whatsapp(pedido.farmacia_id)
    if not cfg or not cfg.ativo or not cfg.enviar_saiu_entrega:
        return

    numero = pedido.cliente.telefone
    link_rastreio = url_for("rastreio_cliente", codigo=pedido.codigo_rastreio, _external=True)

    if cfg.nome_template_saiu_entrega:
        enviar_template_whatsapp(
            numero=numero,
            farmacia_id=pedido.farmacia_id,
            template_nome=cfg.nome_template_saiu_entrega,
            pedido_id=pedido.id,
            tipo="saiu_entrega"
        )

    mensagem = (
        f"Olá, {pedido.cliente.nome}!\n\n"
        f"Seu pedido saiu para entrega.\n"
        f"Acompanhe em tempo real aqui:\n{link_rastreio}"
    )

    enviar_texto_whatsapp(
        numero=numero,
        mensagem=mensagem,
        farmacia_id=pedido.farmacia_id,
        pedido_id=pedido.id,
        tipo="saiu_entrega_link"
    )


def disparar_whatsapp_pedido_entregue(pedido):
    cfg = obter_config_whatsapp(pedido.farmacia_id)
    if not cfg or not cfg.ativo or not cfg.enviar_pedido_entregue:
        return

    numero = pedido.cliente.telefone
    link_rastreio = url_for("rastreio_cliente", codigo=pedido.codigo_rastreio, _external=True)

    if cfg.nome_template_pedido_entregue:
        enviar_template_whatsapp(
            numero=numero,
            farmacia_id=pedido.farmacia_id,
            template_nome=cfg.nome_template_pedido_entregue,
            pedido_id=pedido.id,
            tipo="pedido_entregue"
        )

    mensagem = (
        f"Olá, {pedido.cliente.nome}!\n\n"
        f"Seu pedido foi entregue com sucesso.\n"
        f"Se quiser acompanhar o histórico do pedido, acesse:\n{link_rastreio}"
    )

    enviar_texto_whatsapp(
        numero=numero,
        mensagem=mensagem,
        farmacia_id=pedido.farmacia_id,
        pedido_id=pedido.id,
        tipo="pedido_entregue_link"
    )


def push_habilitado():
    return bool(
        os.environ.get("VAPID_PUBLIC_KEY") and
        os.environ.get("VAPID_PRIVATE_KEY") and
        os.environ.get("VAPID_CLAIMS_SUB")
    )


def enviar_push_para_entregador(entregador_id, titulo, corpo, url="/entregador/app", tag="farmacontrol-push"):
    if not push_habilitado():
        return

    subscriptions = EntregadorPushSubscription.query.filter_by(
        entregador_id=entregador_id,
        ativo=True
    ).all()

    if not subscriptions:
        return

    payload = json.dumps({
        "title": titulo,
        "body": corpo,
        "url": url,
        "tag": tag
    })

    vapid_private_key = os.environ.get("VAPID_PRIVATE_KEY")
    vapid_claims = {
        "sub": os.environ.get("VAPID_CLAIMS_SUB")
    }

    alterou = False

    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {
                "p256dh": sub.p256dh,
                "auth": sub.auth
            }
        }

        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims
            )
        except WebPushException as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            if status_code in [404, 410]:
                sub.ativo = False
                alterou = True

    if alterou:
        db.session.commit()


def registrar_rotas(app):

    @app.route("/")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

   
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            email = request.form.get("email", "").strip()
            senha = request.form.get("senha", "").strip()

            user = User.query.filter_by(email=email, ativo=True).first()
            if user and user.check_password(senha):
                if not user.is_master:
                    ids = []
                    if hasattr(user, "farmacias_ids"):
                        ids = list(user.farmacias_ids or [])

                    if not ids and user.farmacia_id:
                        ids = [user.farmacia_id]

                    if not ids:
                        flash("Usuário sem farmácia vinculada.", "danger")
                        return redirect(url_for("login"))

                    farmacias_ativas = Farmacia.query.filter(
                        Farmacia.id.in_(ids),
                        Farmacia.ativo.is_(True),
                        Farmacia.status == "ativa"
                    ).all()

                    if not farmacias_ativas:
                        flash("Nenhuma farmácia ativa vinculada a este usuário.", "danger")
                        return redirect(url_for("login"))

                login_user(user)

                if not user.is_master:
                    ids = farmacias_ids_do_usuario()
                    if ids:
                        session["farmacia_ativa_id"] = ids[0]

                flash("Login realizado com sucesso.", "success")
                return redirect(url_for("dashboard"))

            flash("E-mail ou senha inválidos.", "danger")

        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        session.pop("farmacia_ativa_id", None)
        session.pop("entregador_id", None)
        session.pop("entregador_farmacia_id", None)
        return redirect(url_for("login"))

    from datetime import datetime, timedelta
    from sqlalchemy import func

    @app.route("/dashboard")
    @login_required
    def dashboard():

        # 🔥 MASTER
        if current_user.is_master:
            total_farmacias = Farmacia.query.count()
            total_farmacias_ativas = Farmacia.query.filter_by(ativo=True, status="ativa").count()
            total_usuarios = User.query.filter(User.perfil != "master").count()
            total_clientes = Cliente.query.count()
            total_entregadores = Entregador.query.count()
            total_pedidos = Pedido.query.count()

            return render_template(
                "master_dashboard.html",
                total_farmacias=total_farmacias,
                total_farmacias_ativas=total_farmacias_ativas,
                total_usuarios=total_usuarios,
                total_clientes=total_clientes,
                total_entregadores=total_entregadores,
                total_pedidos=total_pedidos,
                farmacias=Farmacia.query.order_by(Farmacia.id.desc()).limit(10).all()
            )

        # 🔥 FARMÁCIA
        farmacias_usuario = farmacias_do_usuario_logado()
        ids = [f.id for f in farmacias_usuario]

        if not ids:
            flash("Nenhuma farmácia ativa vinculada ao seu login.", "danger")
            return redirect(url_for("logout"))

        farmacia_param = request.args.get("farmacia_id", "").strip()

        if len(ids) == 1:
            session["farmacia_ativa_id"] = ids[0]
            farmacia_id_filtrada = ids[0]
        else:
            if farmacia_param.lower() == "todas":
                session.pop("farmacia_ativa_id", None)
                farmacia_id_filtrada = None
            elif farmacia_param:
                try:
                    farmacia_id_teste = int(farmacia_param)
                    if farmacia_id_teste in ids:
                        session["farmacia_ativa_id"] = farmacia_id_teste
                        farmacia_id_filtrada = farmacia_id_teste
                    else:
                        farmacia_id_filtrada = session.get("farmacia_ativa_id")
                except ValueError:
                    farmacia_id_filtrada = session.get("farmacia_ativa_id")
            else:
                farmacia_id_filtrada = session.get("farmacia_ativa_id")

        if farmacia_id_filtrada and farmacia_id_filtrada not in ids:
            farmacia_id_filtrada = ids[0]
            session["farmacia_ativa_id"] = farmacia_id_filtrada

        if farmacia_id_filtrada:
            ids_consulta = [farmacia_id_filtrada]
            farmacia = db.session.get(Farmacia, farmacia_id_filtrada)
        else:
            ids_consulta = ids
            farmacia = None

        # 🔹 KPIs básicos
        total_clientes = Cliente.query.filter(Cliente.farmacia_id.in_(ids_consulta)).count()

        total_entregadores = db.session.query(Entregador.id).join(
            EntregadorFarmacia, EntregadorFarmacia.entregador_id == Entregador.id
        ).filter(
            EntregadorFarmacia.farmacia_id.in_(ids_consulta),
            EntregadorFarmacia.ativo.is_(True),
            Entregador.ativo.is_(True)
        ).distinct().count()

        total_pedidos = Pedido.query.filter(Pedido.farmacia_id.in_(ids_consulta)).count()

        pedidos_recebidos = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.status == "recebido"
        ).count()

        pedidos_separacao = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.status == "separacao"
        ).count()

        pedidos_entrega = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.status == "saiu_entrega"
        ).count()

        pedidos_entregues = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.status == "entregue"
        ).count()

        ultimos_pedidos = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta)
        ).order_by(Pedido.id.desc()).limit(10).all()

        # 🔥 GRÁFICO
        hoje_dt = datetime.utcnow()
        hoje = hoje_dt.date()
        dados_grafico = []

        for i in range(6, -1, -1):
            dia = hoje_dt - timedelta(days=i)

            total = db.session.query(func.count(Pedido.id)).filter(
                Pedido.farmacia_id.in_(ids_consulta),
                func.date(Pedido.criado_em) == dia.date()
            ).scalar()

            dados_grafico.append({
                "dia": dia.strftime("%d/%m"),
                "total": total or 0
            })

        # 🔥 PERÍODOS
        inicio_hoje = datetime.combine(hoje, datetime.min.time())
        inicio_amanha = inicio_hoje + timedelta(days=1)
        inicio_ontem = inicio_hoje - timedelta(days=1)

        inicio_semana = inicio_hoje - timedelta(days=hoje.weekday())
        inicio_proxima_semana = inicio_semana + timedelta(days=7)
        inicio_semana_anterior = inicio_semana - timedelta(days=7)

        inicio_mes = inicio_hoje.replace(day=1)

        if inicio_mes.month == 12:
            inicio_proximo_mes = inicio_mes.replace(year=inicio_mes.year + 1, month=1)
        else:
            inicio_proximo_mes = inicio_mes.replace(month=inicio_mes.month + 1)

        if inicio_mes.month == 1:
            inicio_mes_anterior = inicio_mes.replace(year=inicio_mes.year - 1, month=12)
        else:
            inicio_mes_anterior = inicio_mes.replace(month=inicio_mes.month - 1)

        # 🔥 CONTAGENS
        pedidos_hoje = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.criado_em >= inicio_hoje,
            Pedido.criado_em < inicio_amanha
        ).count()

        pedidos_ontem = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.criado_em >= inicio_ontem,
            Pedido.criado_em < inicio_hoje
        ).count()

        pedidos_semana = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.criado_em >= inicio_semana,
            Pedido.criado_em < inicio_proxima_semana
        ).count()

        pedidos_semana_anterior = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.criado_em >= inicio_semana_anterior,
            Pedido.criado_em < inicio_semana
        ).count()

        pedidos_mes = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.criado_em >= inicio_mes,
            Pedido.criado_em < inicio_proximo_mes
        ).count()

        pedidos_mes_anterior = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.criado_em >= inicio_mes_anterior,
            Pedido.criado_em < inicio_mes
        ).count()

        def calc(atual, anterior):
            if anterior == 0:
                return 100 if atual > 0 else 0
            return round(((atual - anterior) / anterior) * 100, 1)

        crescimento_dia = calc(pedidos_hoje, pedidos_ontem)
        crescimento_semana = calc(pedidos_semana, pedidos_semana_anterior)
        crescimento_mes = calc(pedidos_mes, pedidos_mes_anterior)

        # 🔥 TEMPO MÉDIO
        entregues = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.status == "entregue",
            Pedido.saiu_entrega_em.isnot(None),
            Pedido.entregue_em.isnot(None)
        ).all()

        tempos = [
            int((p.entregue_em - p.saiu_entrega_em).total_seconds() // 60)
            for p in entregues if p.entregue_em and p.saiu_entrega_em
        ]

        tempo_medio_entrega_min = round(sum(tempos)/len(tempos),1) if tempos else 0

        # 🔥 RANKING
        ranking_raw = db.session.query(
            Entregador.id,
            Entregador.nome,
            func.count(Pedido.id)
        ).join(Pedido).filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.status == "entregue"
        ).group_by(Entregador.id).order_by(func.count(Pedido.id).desc()).limit(5).all()

        ranking_entregadores = []
        for id_e, nome, total in ranking_raw:
            pedidos_e = Pedido.query.filter_by(entregador_id=id_e, status="entregue").all()
            tempos_e = [
                int((p.entregue_em - p.saiu_entrega_em).total_seconds() // 60)
                for p in pedidos_e if p.entregue_em and p.saiu_entrega_em
            ]
            media = round(sum(tempos_e)/len(tempos_e),1) if tempos_e else 0

            ranking_entregadores.append({
                "nome": nome,
                "total_entregas": total,
                "tempo_medio": media
            })

        melhor_entregador = ranking_entregadores[0] if ranking_entregadores else None

        return render_template(
            "dashboard.html",
            farmacia=farmacia,
            farmacias_usuario=farmacias_usuario,
            farmacia_ativa_id=farmacia_id_filtrada,
            exibindo_todas_farmacias=(farmacia_id_filtrada is None and len(ids) > 1),

            total_clientes=total_clientes,
            total_entregadores=total_entregadores,
            total_pedidos=total_pedidos,

            pedidos_recebidos=pedidos_recebidos,
            pedidos_separacao=pedidos_separacao,
            pedidos_entrega=pedidos_entrega,
            pedidos_entregues=pedidos_entregues,

            ultimos_pedidos=ultimos_pedidos,
            dados_grafico=dados_grafico,

            pedidos_hoje=pedidos_hoje,
            pedidos_ontem=pedidos_ontem,
            crescimento_dia=crescimento_dia,

            pedidos_semana=pedidos_semana,
            pedidos_semana_anterior=pedidos_semana_anterior,
            crescimento_semana=crescimento_semana,

            pedidos_mes=pedidos_mes,
            pedidos_mes_anterior=pedidos_mes_anterior,
            crescimento_mes=crescimento_mes,

            tempo_medio_entrega_min=tempo_medio_entrega_min,
            ranking_entregadores=ranking_entregadores,
            melhor_entregador=melhor_entregador
        )

    # =========================
    # MASTER - FARMÁCIAS
    # =========================
    @app.route("/master/farmacias", methods=["GET", "POST"])
    @login_required
    @master_required
    def master_farmacias():
        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            cnpj = request.form.get("cnpj", "").strip()
            telefone = request.form.get("telefone", "").strip()
            email = request.form.get("email", "").strip()
            endereco = request.form.get("endereco", "").strip()
            cidade = request.form.get("cidade", "").strip()
            plano = request.form.get("plano", "basico").strip()
            status = request.form.get("status", "ativa").strip()

            if not nome:
                flash("Nome da farmácia é obrigatório.", "warning")
                return redirect(url_for("master_farmacias"))

            if cnpj:
                existe_cnpj = Farmacia.query.filter_by(cnpj=cnpj).first()
                if existe_cnpj:
                    flash("Já existe uma farmácia com esse CNPJ.", "danger")
                    return redirect(url_for("master_farmacias"))

            nova = Farmacia(
                nome=nome,
                cnpj=cnpj or None,
                telefone=telefone or None,
                email=email or None,
                endereco=endereco or None,
                cidade=cidade or None,
                plano=plano or "basico",
                status=status or "ativa",
                ativo=(status == "ativa")
            )
            db.session.add(nova)
            db.session.commit()

            garantir_whatsapp_config(nova.id)

            flash("Farmácia cadastrada com sucesso.", "success")
            return redirect(url_for("master_farmacias"))

        farmacias = Farmacia.query.order_by(Farmacia.id.desc()).all()
        return render_template("master_farmacias.html", farmacias=farmacias)


    @app.route("/master/farmacia/<int:farmacia_id>/toggle")
    @login_required
    @master_required
    def master_toggle_farmacia(farmacia_id):
        farmacia = db.session.get(Farmacia, farmacia_id)
        if not farmacia:
            flash("Farmácia não encontrada.", "danger")
            return redirect(url_for("master_farmacias"))

        farmacia.ativo = not farmacia.ativo
        farmacia.status = "ativa" if farmacia.ativo else "inativa"
        db.session.commit()

        flash("Status da farmácia atualizado.", "success")
        return redirect(url_for("master_farmacias"))


    @app.route("/master/farmacia/<int:farmacia_id>/editar", methods=["GET", "POST"])
    @login_required
    @master_required
    def master_editar_farmacia(farmacia_id):
        farmacia = db.session.get(Farmacia, farmacia_id)
        if not farmacia:
            flash("Farmácia não encontrada.", "danger")
            return redirect(url_for("master_farmacias"))

        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            cnpj = request.form.get("cnpj", "").strip()
            telefone = request.form.get("telefone", "").strip()
            email = request.form.get("email", "").strip()
            endereco = request.form.get("endereco", "").strip()
            cidade = request.form.get("cidade", "").strip()
            plano = request.form.get("plano", "basico").strip()
            status = request.form.get("status", "ativa").strip()

            if not nome:
                flash("Nome da farmácia é obrigatório.", "warning")
                return redirect(url_for("master_editar_farmacia", farmacia_id=farmacia.id))

            if cnpj:
                existe_cnpj = Farmacia.query.filter(
                    Farmacia.cnpj == cnpj,
                    Farmacia.id != farmacia.id
                ).first()
                if existe_cnpj:
                    flash("Já existe outra farmácia com esse CNPJ.", "danger")
                    return redirect(url_for("master_editar_farmacia", farmacia_id=farmacia.id))

            farmacia.nome = nome
            farmacia.cnpj = cnpj or None
            farmacia.telefone = telefone or None
            farmacia.email = email or None
            farmacia.endereco = endereco or None
            farmacia.cidade = cidade or None
            farmacia.plano = plano or "basico"
            farmacia.status = status or "ativa"
            farmacia.ativo = (farmacia.status == "ativa")

            db.session.commit()
            flash("Farmácia atualizada com sucesso.", "success")
            return redirect(url_for("master_farmacias"))

        return render_template("master_farmacia_editar.html", farmacia=farmacia)


    @app.route("/master/farmacia/<int:farmacia_id>/apagar", methods=["POST"])
    @login_required
    @master_required
    def master_apagar_farmacia(farmacia_id):
        farmacia = db.session.get(Farmacia, farmacia_id)
        if not farmacia:
            flash("Farmácia não encontrada.", "danger")
            return redirect(url_for("master_farmacias"))

        db.session.delete(farmacia)
        db.session.commit()

        flash("Farmácia apagada com sucesso.", "success")
        return redirect(url_for("master_farmacias"))

    # =========================
    # MASTER - USUÁRIOS
    # =========================
    @app.route("/master/usuarios", methods=["GET", "POST"])
    @login_required
    @master_required
    def master_usuarios():

        if request.method == "POST":

            nome = request.form.get("nome", "").strip()
            email = request.form.get("email", "").strip()
            senha = request.form.get("senha", "").strip()
            perfil = request.form.get("perfil", "admin").strip()

            farmacias_ids = request.form.getlist("farmacias_ids")

            if not nome or not email or not senha:
                flash("Preencha nome, e-mail e senha.", "warning")
                return redirect(url_for("master_usuarios"))

            if not farmacias_ids:
                flash("Selecione pelo menos uma farmácia.", "warning")
                return redirect(url_for("master_usuarios"))

            if User.query.filter_by(email=email).first():
                flash("Já existe usuário com esse e-mail.", "danger")
                return redirect(url_for("master_usuarios"))

            primeira_farmacia_id = int(farmacias_ids[0])

            novo = User(
                nome=nome,
                email=email,
                perfil="admin",
                farmacia_id=primeira_farmacia_id,
                ativo=True
            )

            novo.set_password(senha)

            db.session.add(novo)
            db.session.flush()

            for fid in farmacias_ids:
                farmacia = db.session.get(Farmacia, int(fid))

                if farmacia:
                    vinculo = UsuarioFarmacia(
                        usuario_id=novo.id,
                        farmacia_id=farmacia.id,
                        perfil="admin",
                        ativo=True
                    )
                    db.session.add(vinculo)

            db.session.commit()

            flash("Usuário cadastrado com acesso às farmácias selecionadas.", "success")
            return redirect(url_for("master_usuarios"))

        usuarios = User.query.filter(User.perfil != "master").order_by(User.id.desc()).all()
        farmacias = Farmacia.query.filter_by(ativo=True).order_by(Farmacia.nome.asc()).all()

        return render_template(
            "master_usuarios.html",
            usuarios=usuarios,
            farmacias=farmacias
        )

    # =========================
    # CLIENTES
    # =========================
    @app.route("/clientes", methods=["GET", "POST"])
    @login_required
    def clientes():
        if current_user.is_master:
            flash("Área disponível apenas para usuários de farmácia.", "warning")
            return redirect(url_for("dashboard"))

        farmacia_id = farmacia_ativa_id()
        if not farmacia_id:
            flash("Nenhuma farmácia ativa selecionada.", "warning")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            telefone = request.form.get("telefone", "").strip()
            endereco = request.form.get("endereco", "").strip()
            bairro = request.form.get("bairro", "").strip()

            telefone = normalizar_telefone_br(telefone)

            if not nome or not telefone or not endereco:
                flash("Preencha nome, telefone e endereço do cliente.", "warning")
                return redirect(url_for("clientes"))

            if len("".join(ch for ch in telefone if ch.isdigit())) < 12:
                flash("Informe um telefone válido com DDD e número.", "warning")
                return redirect(url_for("clientes"))

            novo = Cliente(
                farmacia_id=farmacia_id,
                nome=nome,
                telefone=telefone,
                endereco=endereco,
                bairro=bairro if bairro else None
            )
            db.session.add(novo)
            db.session.commit()

            flash("Cliente cadastrado com sucesso.", "success")
            return redirect(url_for("clientes"))

        lista = Cliente.query.filter_by(
            farmacia_id=farmacia_id
        ).order_by(Cliente.id.desc()).all()

        return render_template("clientes.html", clientes=lista)

    # =========================
    # ENTREGADORES
    # =========================
    @app.route("/entregadores", methods=["GET", "POST"])
    @login_required
    def entregadores():
        if current_user.is_master:
            flash("Área disponível apenas para usuários de farmácia.", "warning")
            return redirect(url_for("dashboard"))

        farmacias_usuario = farmacias_do_usuario_logado()
        farmacias_permitidas_ids = [f.id for f in farmacias_usuario]

        if not farmacias_permitidas_ids:
            flash("Nenhuma farmácia disponível para este usuário.", "warning")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            telefone = request.form.get("telefone", "").strip()
            senha = request.form.get("senha", "").strip()
            farmacias_ids = request.form.getlist("farmacias_ids")

            if not nome or not telefone or not senha:
                flash("Preencha todos os campos do entregador.", "warning")
                return redirect(url_for("entregadores"))

            if not farmacias_ids:
                flash("Selecione pelo menos uma farmácia.", "warning")
                return redirect(url_for("entregadores"))

            farmacias_ids = [int(fid) for fid in farmacias_ids if fid.isdigit()]
            farmacias_ids = [fid for fid in farmacias_ids if fid in farmacias_permitidas_ids]

            if not farmacias_ids:
                flash("Nenhuma farmácia válida foi selecionada.", "danger")
                return redirect(url_for("entregadores"))

            entregador_existente = Entregador.query.filter_by(
                telefone=telefone
            ).first()

            if entregador_existente:
                if not entregador_existente.check_password(senha):
                    flash("Já existe entregador com esse telefone, mas a senha informada é diferente.", "danger")
                    return redirect(url_for("entregadores"))

                if nome:
                    entregador_existente.nome = nome

                if not entregador_existente.farmacia_id:
                    entregador_existente.farmacia_id = farmacias_ids[0]

                adicionou = False

                for farmacia_id in farmacias_ids:
                    vinculo_existente = EntregadorFarmacia.query.filter_by(
                        entregador_id=entregador_existente.id,
                        farmacia_id=farmacia_id
                    ).first()

                    if not vinculo_existente:
                        vinculo = EntregadorFarmacia(
                            entregador_id=entregador_existente.id,
                            farmacia_id=farmacia_id,
                            ativo=True
                        )
                        db.session.add(vinculo)
                        adicionou = True

                db.session.commit()

                if adicionou:
                    flash("Entregador existente vinculado às farmácias selecionadas com sucesso.", "success")
                else:
                    flash("Esse entregador já estava vinculado às farmácias selecionadas.", "warning")

                return redirect(url_for("entregadores"))

            novo = Entregador(
                farmacia_id=farmacias_ids[0],
                nome=nome,
                telefone=telefone,
                ativo=True
            )
            novo.set_password(senha)

            db.session.add(novo)
            db.session.flush()

            for farmacia_id in farmacias_ids:
                vinculo = EntregadorFarmacia(
                    entregador_id=novo.id,
                    farmacia_id=farmacia_id,
                    ativo=True
                )
                db.session.add(vinculo)

            db.session.commit()

            flash("Entregador cadastrado com sucesso.", "success")
            return redirect(url_for("entregadores"))

        entregadores_ids = [
            v.entregador_id
            for v in EntregadorFarmacia.query.filter(
                EntregadorFarmacia.farmacia_id.in_(farmacias_permitidas_ids),
                EntregadorFarmacia.ativo.is_(True)
            ).all()
        ]

        if entregadores_ids:
            lista = Entregador.query.filter(
                Entregador.id.in_(entregadores_ids)
            ).order_by(Entregador.id.desc()).all()
        else:
            lista = []

        return render_template(
            "entregadores.html",
            entregadores=lista,
            farmacias_usuario=farmacias_usuario
        )
    # =========================
    # PEDIDOS
    # =========================
    @app.route("/api/pedidos/ultimo")
    @login_required
    def ultimo_pedido():
        if current_user.is_master:
            ultimo = Pedido.query.order_by(Pedido.id.desc()).first()
        else:
            ids = farmacias_ids_do_usuario()
            if not ids:
                return jsonify({"ok": True, "pedido": None})

            ultimo = Pedido.query.filter(
                Pedido.farmacia_id.in_(ids)
            ).order_by(Pedido.id.desc()).first()

        if not ultimo:
            return jsonify({"ok": True, "pedido": None})

        return jsonify({
            "ok": True,
            "pedido": {
                "id": ultimo.id,
                "cliente": ultimo.cliente.nome,
                "status": ultimo.status,
                "criado_em": ultimo.criado_em.strftime("%d/%m/%Y %H:%M:%S") if ultimo.criado_em else ""
            }
        })

    @app.route("/pedidos", methods=["GET", "POST"])
    @login_required
    def pedidos():
        if current_user.is_master:
            flash("Área disponível apenas para usuários de farmácia.", "warning")
            return redirect(url_for("dashboard"))

        farmacia_id = farmacia_ativa_id()
        if not farmacia_id:
            flash("Nenhuma farmácia ativa selecionada.", "warning")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            cliente_id = request.form.get("cliente_id")
            entregador_id = request.form.get("entregador_id")
            status = request.form.get("status", "recebido").strip()

            if not cliente_id:
                flash("Cliente é obrigatório.", "warning")
                return redirect(url_for("pedidos"))

            if not entregador_id:
                flash("SELECIONE O ENTREGADOR", "danger")
                return redirect(url_for("pedidos"))

            cliente = Cliente.query.filter_by(
                id=int(cliente_id),
                farmacia_id=farmacia_id
            ).first()

            if not cliente:
                flash("Cliente inválido para esta farmácia.", "danger")
                return redirect(url_for("pedidos"))

            entregador = Entregador.query.filter_by(
                id=int(entregador_id),
                ativo=True
            ).first()

            if not entregador:
                flash("Entregador inválido.", "danger")
                return redirect(url_for("pedidos"))

            vinculo_entregador = EntregadorFarmacia.query.filter_by(
                entregador_id=entregador.id,
                farmacia_id=farmacia_id,
                ativo=True
            ).first()

            if not vinculo_entregador:
                flash("Entregador não está vinculado a esta farmácia.", "danger")
                return redirect(url_for("pedidos"))

            codigo = gerar_codigo_rastreio()
            while Pedido.query.filter_by(codigo_rastreio=codigo).first():
                codigo = gerar_codigo_rastreio()

            novo = Pedido(
                farmacia_id=farmacia_id,
                cliente_id=cliente.id,
                entregador_id=entregador.id,
                status=status,
                codigo_rastreio=codigo
            )

            if status == "saiu_entrega":
                novo.saiu_entrega_em = agora_brasil()
            elif status == "entregue":
                novo.entregue_em = agora_brasil()

            db.session.add(novo)
            db.session.commit()

            if novo.entregador_id:
                enviar_push_para_entregador(
                    entregador_id=novo.entregador_id,
                    titulo="Novo pedido para entrega",
                    corpo=f"Pedido #{novo.id} para {novo.cliente.nome}",
                    url=url_for("entregador_app", _external=True),
                    tag=f"pedido-novo-{novo.id}"
                )

            if novo.status == "recebido":
                disparar_whatsapp_pedido_recebido(novo)

            if novo.status == "saiu_entrega":
                disparar_whatsapp_saiu_entrega(novo)

            if novo.status == "entregue":
                disparar_whatsapp_pedido_entregue(novo)

            flash("Pedido cadastrado com sucesso.", "success")
            return redirect(url_for("pedidos"))

        lista = Pedido.query.filter_by(
            farmacia_id=farmacia_id
        ).order_by(Pedido.id.desc()).all()

        clientes_lista = Cliente.query.filter_by(
            farmacia_id=farmacia_id
        ).order_by(Cliente.nome.asc()).all()

        entregadores_ids = [
            v.entregador_id
            for v in EntregadorFarmacia.query.filter_by(
                farmacia_id=farmacia_id,
                ativo=True
            ).all()
        ]

        if entregadores_ids:
            entregadores_lista = Entregador.query.filter(
                Entregador.id.in_(entregadores_ids),
                Entregador.ativo.is_(True)
            ).order_by(Entregador.nome.asc()).all()
        else:
            entregadores_lista = []

        return render_template(
            "pedidos.html",
            pedidos=lista,
            clientes=clientes_lista,
            entregadores=entregadores_lista
        )
    
    @app.route("/backup")
    @login_required
    def backup():
        import json

        dados = {
            "clientes": [
                {"nome": c.nome, "telefone": c.telefone, "endereco": c.endereco}
                for c in Cliente.query.all()
            ],
            "entregadores": [
                {"nome": e.nome, "telefone": e.telefone}
                for e in Entregador.query.all()
            ],
        }

        return json.dumps(dados, indent=2)

    @app.route("/pedido/<int:pedido_id>/status", methods=["POST"])
    @login_required
    def atualizar_status_pedido(pedido_id):
        pedido = Pedido.query.get_or_404(pedido_id)

        if not validar_acesso_farmacia(pedido.farmacia_id):
            abort(403)

        status_anterior = pedido.status
        novo_status = request.form.get("status", "").strip()

        if novo_status not in ["recebido", "separacao", "saiu_entrega", "entregue"]:
            flash("Status inválido.", "danger")
            return redirect(url_for("pedidos"))

        pedido.status = novo_status

        if novo_status == "saiu_entrega" and not pedido.saiu_entrega_em:
            pedido.saiu_entrega_em = agora_brasil()

        if novo_status == "entregue":
            pedido.entregue_em = agora_brasil()

        db.session.commit()

        # RECEBIDO: envia sempre que alguém marcar como recebido
        if novo_status == "recebido":
            enviar_whatsapp_template(
                pedido.cliente.telefone,
                "pedido_recebido",
                [
                    pedido.cliente.nome,
                    pedido.id
                ]
            )

        if pedido.entregador_id:
            status_label = novo_status
            if novo_status == "recebido":
                status_label = "RECEBIDO"
            elif novo_status == "separacao":
                status_label = "SEPARAÇÃO"
            elif novo_status == "saiu_entrega":
                status_label = "EM ROTA"
            elif novo_status == "entregue":
                status_label = "ENTREGUE"

            enviar_push_para_entregador(
                entregador_id=pedido.entregador_id,
                titulo="Status do pedido atualizado",
                corpo=f"Pedido #{pedido.id} agora está {status_label}",
                url=url_for("entregador_app", _external=True),
                tag=f"pedido-status-{pedido.id}"
            )

        if status_anterior != novo_status:
            if novo_status == "saiu_entrega":
                link = url_for("rastreio_cliente", codigo=pedido.codigo_rastreio, _external=True)
                enviar_whatsapp_template(
                    pedido.cliente.telefone,
                    "pedido_saiu_entrega",
                    [
                        pedido.cliente.nome,
                        pedido.id,
                        link
                    ]
                )

            elif novo_status == "entregue":
                enviar_whatsapp_template(
                    pedido.cliente.telefone,
                    "pedido_entregue",
                    [
                        pedido.cliente.nome,
                        pedido.id
                    ]
                )

        flash("Status do pedido atualizado.", "success")
        return redirect(url_for("pedidos"))

    # =========================
    # WHATSAPP
    # =========================
    @app.route("/whatsapp/config", methods=["GET", "POST"])
    @login_required
    def whatsapp_config():
        if current_user.is_master:
            flash("Área disponível apenas para usuários de farmácia.", "warning")
            return redirect(url_for("dashboard"))

        farmacia_id = farmacia_ativa_id()
        if not farmacia_id:
            flash("Nenhuma farmácia ativa selecionada.", "warning")
            return redirect(url_for("dashboard"))

        garantir_whatsapp_config(farmacia_id)
        cfg = obter_config_whatsapp(farmacia_id)

        if request.method == "POST":
            cfg.ativo = bool(request.form.get("ativo"))
            cfg.access_token = request.form.get("access_token", "").strip()
            cfg.phone_number_id = request.form.get("phone_number_id", "").strip()
            cfg.business_account_id = request.form.get("business_account_id", "").strip()
            cfg.verify_token = request.form.get("verify_token", "").strip()

            cfg.nome_template_pedido_recebido = request.form.get("nome_template_pedido_recebido", "").strip()
            cfg.nome_template_saiu_entrega = request.form.get("nome_template_saiu_entrega", "").strip()
            cfg.nome_template_pedido_entregue = request.form.get("nome_template_pedido_entregue", "").strip()

            cfg.enviar_pedido_recebido = bool(request.form.get("enviar_pedido_recebido"))
            cfg.enviar_saiu_entrega = bool(request.form.get("enviar_saiu_entrega"))
            cfg.enviar_pedido_entregue = bool(request.form.get("enviar_pedido_entregue"))

            db.session.commit()
            flash("Configurações do WhatsApp salvas com sucesso.", "success")
            return redirect(url_for("whatsapp_config"))

        logs = WhatsAppLog.query.filter_by(
            farmacia_id=farmacia_id
        ).order_by(WhatsAppLog.id.desc()).limit(20).all()

        return render_template(
            "whatsapp_config.html",
            cfg=cfg,
            logs=logs
        )

    @app.route("/whatsapp/teste", methods=["POST"])
    @login_required
    def whatsapp_teste():
        if current_user.is_master:
            flash("Área disponível apenas para usuários de farmácia.", "warning")
            return redirect(url_for("dashboard"))

        farmacia_id = farmacia_ativa_id()
        if not farmacia_id:
            flash("Nenhuma farmácia ativa selecionada.", "warning")
            return redirect(url_for("dashboard"))

        numero = request.form.get("numero", "").strip()
        mensagem = request.form.get("mensagem", "").strip()

        if not numero or not mensagem:
            flash("Informe número e mensagem para o teste.", "warning")
            return redirect(url_for("whatsapp_config"))

        resultado = enviar_texto_whatsapp(
            numero=numero,
            mensagem=mensagem,
            farmacia_id=farmacia_id,
            tipo="teste_manual"
        )

        if resultado["ok"]:
            flash("Mensagem de teste enviada.", "success")
        else:
            flash(resultado["mensagem"], "danger")

        return redirect(url_for("whatsapp_config"))

    @app.route("/webhook/whatsapp", methods=["GET"])
    def verificar_webhook_whatsapp():
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        cfg = WhatsAppConfig.query.filter_by(verify_token=token).first()

        if mode == "subscribe" and cfg:
            return challenge, 200

        return "Token inválido", 403

    @app.route("/webhook/whatsapp", methods=["POST"])
    def receber_webhook_whatsapp():
        payload = request.get_json(silent=True) or {}

        criar_log_whatsapp(
            tipo="webhook_recebido",
            farmacia_id=None,
            mensagem=json.dumps(payload, ensure_ascii=False),
            status="recebido",
            direction="inbound"
        )

        return jsonify({"ok": True}), 200

    @app.route("/pedido/<int:pedido_id>/whatsapp-localizacao")
    def whatsapp_cliente_localizacao(pedido_id):
        entregador_id = session.get("entregador_id")
        farmacia_id = session.get("entregador_farmacia_id")

        if not entregador_id or not farmacia_id:
            flash("Faça login como entregador.", "warning")
            return redirect(url_for("entregador_login"))

        pedido = Pedido.query.filter_by(
            id=pedido_id,
            farmacia_id=farmacia_id
        ).first_or_404()

        if pedido.entregador_id != entregador_id:
            flash("Pedido não pertence a este entregador.", "danger")
            return redirect(url_for("entregador_app"))

        mensagem = (
            f"Olá, {pedido.cliente.nome}! "
            f"Sou o entregador da farmácia. "
            f"Para facilitar a entrega do seu pedido #{pedido.id}, "
            f"poderia enviar sua localização atual por aqui? 📍"
        )

        return redirect(link_whatsapp(pedido.cliente.telefone, mensagem))

    # =========================
    # APP DO ENTREGADOR
    # =========================
    @app.route("/entregador")
    def entregador_redirect():
        return redirect(url_for("entregador_login"))

    @app.route("/entregador/login", methods=["GET", "POST"])
    def entregador_login():
        if request.method == "POST":
            telefone = request.form.get("telefone", "").strip()
            senha = request.form.get("senha", "").strip()

            if not telefone or not senha:
                flash("Preencha telefone e senha.", "warning")
                return redirect(url_for("entregador_login"))

            entregadores = Entregador.query.filter_by(
                telefone=telefone,
                ativo=True
            ).all()

            entregador_valido = None

            for entregador in entregadores:
                if entregador.check_password(senha):
                    if entregador.farmacias_ids:
                        entregador_valido = entregador
                        break

            if entregador_valido:
                session.clear()
                session.permanent = True
                session["entregador_id"] = entregador_valido.id
                flash("Login realizado com sucesso.", "success")
                return redirect(url_for("entregador_app"))

            flash("Dados inválidos.", "danger")

        return render_template("entregador_login.html")

    @app.route("/entregador/logout")
    def entregador_logout():
        session.clear()
        flash("Você saiu da área do entregador.", "info")
        return redirect(url_for("entregador_login"))

    @app.route("/entregador/app")
    def entregador_app():
        entregador_id = session.get("entregador_id")

        if not entregador_id:
            flash("Faça login como entregador.", "warning")
            return redirect(url_for("entregador_login"))

        entregador = Entregador.query.filter_by(
            id=entregador_id,
            ativo=True
        ).first_or_404()

        farmacias_ids = entregador.farmacias_ids
        if not farmacias_ids:
            flash("Entregador sem farmácias vinculadas.", "warning")
            return redirect(url_for("entregador_logout"))

        farmacia_id_param = request.args.get("farmacia_id", "").strip()

        if farmacia_id_param.lower() == "todas":
            session.pop("entregador_farmacia_id", None)
            farmacia_id_filtrada = None
        elif farmacia_id_param:
            try:
                farmacia_id_teste = int(farmacia_id_param)
                if farmacia_id_teste in farmacias_ids:
                    session["entregador_farmacia_id"] = farmacia_id_teste
                    farmacia_id_filtrada = farmacia_id_teste
                else:
                    farmacia_id_filtrada = session.get("entregador_farmacia_id")
            except ValueError:
                farmacia_id_filtrada = session.get("entregador_farmacia_id")
        else:
            farmacia_id_filtrada = session.get("entregador_farmacia_id")

        if farmacia_id_filtrada and farmacia_id_filtrada not in farmacias_ids:
            farmacia_id_filtrada = None
            session.pop("entregador_farmacia_id", None)

        if farmacia_id_filtrada:
            ids_consulta = [farmacia_id_filtrada]
            farmacia_ativa = db.session.get(Farmacia, farmacia_id_filtrada)
            farmacia_ativa_nome = farmacia_ativa.nome if farmacia_ativa else None
        else:
            ids_consulta = farmacias_ids
            farmacia_ativa_nome = None

        pedidos = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.entregador_id == entregador.id,
            Pedido.status.in_(["recebido", "separacao", "saiu_entrega"])
        ).order_by(Pedido.id.desc()).all()

        farmacias = Farmacia.query.filter(
            Farmacia.id.in_(farmacias_ids)
        ).order_by(Farmacia.nome.asc()).all()

        return render_template(
            "entregador_app.html",
            entregador=entregador,
            pedidos=pedidos,
            farmacias=farmacias,
            farmacia_ativa_id=farmacia_id_filtrada,
            farmacia_ativa_nome=farmacia_ativa_nome,
            exibindo_todas_farmacias=(farmacia_id_filtrada is None and len(farmacias_ids) > 1)
        )
    
    @app.route("/api/push/public-key")
    def api_push_public_key():
        public_key = os.environ.get("VAPID_PUBLIC_KEY")

        if not public_key:
            return jsonify({
                "ok": False,
                "mensagem": "Push não configurado no servidor."
            }), 200

        return jsonify({
            "ok": True,
            "public_key": public_key
        })


    @app.route("/api/entregador/push/subscribe", methods=["POST"])
    def api_entregador_push_subscribe():
        entregador_id = session.get("entregador_id")

        if not entregador_id:
            return jsonify({"ok": False, "mensagem": "Entregador não autenticado."}), 401

        entregador = Entregador.query.filter_by(
            id=entregador_id,
            ativo=True
        ).first()

        if not entregador:
            return jsonify({"ok": False, "mensagem": "Entregador inválido."}), 401

        data = request.get_json(silent=True) or {}
        endpoint = (data.get("endpoint") or "").strip()
        keys = data.get("keys") or {}

        p256dh = (keys.get("p256dh") or "").strip()
        auth = (keys.get("auth") or "").strip()

        if not endpoint or not p256dh or not auth:
            return jsonify({"ok": False, "mensagem": "Inscrição push inválida."}), 400

        subscription = EntregadorPushSubscription.query.filter_by(endpoint=endpoint).first()

        if subscription:
            subscription.entregador_id = entregador.id
            subscription.p256dh = p256dh
            subscription.auth = auth
            subscription.user_agent = request.headers.get("User-Agent")
            subscription.ativo = True
        else:
            subscription = EntregadorPushSubscription(
                entregador_id=entregador.id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                user_agent=request.headers.get("User-Agent"),
                ativo=True
            )
            db.session.add(subscription)

        db.session.commit()

        return jsonify({"ok": True, "mensagem": "Push ativado com sucesso."})

    @app.route("/api/entregador/alertas")
    def api_entregador_alertas():
        entregador_id = session.get("entregador_id")

        if not entregador_id:
            return jsonify({"ok": False, "mensagem": "Entregador não autenticado."}), 401

        entregador = Entregador.query.filter_by(
            id=entregador_id,
            ativo=True
        ).first()

        if not entregador:
            return jsonify({"ok": False, "mensagem": "Entregador inválido."}), 401

        farmacias_ids = entregador.farmacias_ids
        if not farmacias_ids:
            return jsonify({"ok": True, "pedidos": []})

        farmacia_id_filtrada = session.get("entregador_farmacia_id")

        if farmacia_id_filtrada and farmacia_id_filtrada in farmacias_ids:
            ids_consulta = [farmacia_id_filtrada]
        else:
            ids_consulta = farmacias_ids

        pedidos = Pedido.query.filter(
            Pedido.farmacia_id.in_(ids_consulta),
            Pedido.entregador_id == entregador.id,
            Pedido.status.in_(["recebido", "separacao", "saiu_entrega"])
        ).order_by(Pedido.id.desc()).all()

        resultado = []

        for pedido in pedidos:
            status_label = pedido.status
            if pedido.status == "recebido":
                status_label = "RECEBIDO"
            elif pedido.status == "separacao":
                status_label = "SEPARAÇÃO"
            elif pedido.status == "saiu_entrega":
                status_label = "EM ROTA"
            elif pedido.status == "entregue":
                status_label = "ENTREGUE"

            resultado.append({
                "id": pedido.id,
                "status": pedido.status,
                "status_label": status_label,
                "farmacia_nome": pedido.farmacia.nome if pedido.farmacia else "-",
                "cliente_nome": pedido.cliente.nome if pedido.cliente else "-",
                "endereco": pedido.cliente.endereco if pedido.cliente else "-",
                "bairro": pedido.cliente.bairro if pedido.cliente and pedido.cliente.bairro else ""
            })

        return jsonify({
            "ok": True,
            "pedidos": resultado
        })    

    @app.route("/pedido/<int:pedido_id>/iniciar-entrega", methods=["POST"])
    def iniciar_entrega(pedido_id):
        entregador_id = session.get("entregador_id")

        if not entregador_id:
            return jsonify({"ok": False, "mensagem": "Entregador não autenticado."}), 401

        entregador = Entregador.query.filter_by(
            id=entregador_id,
            ativo=True
        ).first()

        if not entregador:
            return jsonify({"ok": False, "mensagem": "Entregador inválido."}), 401

        pedido = Pedido.query.get_or_404(pedido_id)

        if pedido.entregador_id != entregador.id:
            return jsonify({"ok": False, "mensagem": "Pedido não pertence a este entregador."}), 403

        if pedido.farmacia_id not in entregador.farmacias_ids:
            return jsonify({"ok": False, "mensagem": "Entrega não permitida para esta farmácia."}), 403

        session["entregador_farmacia_id"] = pedido.farmacia_id

        status_anterior = pedido.status
        pedido.status = "saiu_entrega"

        if not pedido.saiu_entrega_em:
            pedido.saiu_entrega_em = agora_brasil()

        db.session.commit()

        if status_anterior != "saiu_entrega":
            disparar_whatsapp_saiu_entrega(pedido)

        return jsonify({
            "ok": True,
            "mensagem": "Entrega iniciada com sucesso.",
            "google_maps_url": link_google_maps(pedido.cliente.endereco),
            "waze_url": link_waze(pedido.cliente.endereco),
            "endereco": pedido.cliente.endereco
        })

    @app.route("/pedido/<int:pedido_id>/finalizar-entrega", methods=["POST"])
    def finalizar_entrega(pedido_id):
        entregador_id = session.get("entregador_id")

        if not entregador_id:
            return jsonify({"ok": False, "mensagem": "Entregador não autenticado."}), 401

        entregador = Entregador.query.filter_by(
            id=entregador_id,
            ativo=True
        ).first()

        if not entregador:
            return jsonify({"ok": False, "mensagem": "Entregador inválido."}), 401

        pedido = Pedido.query.get_or_404(pedido_id)

        if pedido.entregador_id != entregador.id:
            return jsonify({"ok": False, "mensagem": "Pedido não pertence a este entregador."}), 403

        if pedido.farmacia_id not in entregador.farmacias_ids:
            return jsonify({"ok": False, "mensagem": "Entrega não permitida para esta farmácia."}), 403

        session["entregador_farmacia_id"] = pedido.farmacia_id

        status_anterior = pedido.status
        pedido.status = "entregue"
        pedido.entregue_em = agora_brasil()

        db.session.commit()

        if status_anterior != "entregue":
            disparar_whatsapp_pedido_entregue(pedido)

        return jsonify({"ok": True, "mensagem": "Entrega finalizada com sucesso."})

    @app.route("/api/entregador/localizacao", methods=["POST"])
    def salvar_localizacao():
        entregador_id = session.get("entregador_id")

        if not entregador_id:
            return jsonify({"ok": False, "mensagem": "Entregador não autenticado."}), 401

        entregador = Entregador.query.filter_by(
            id=entregador_id,
            ativo=True
        ).first()

        if not entregador:
            return jsonify({"ok": False, "mensagem": "Entregador inválido."}), 401

        data = request.get_json(silent=True) or {}
        latitude = str(data.get("latitude", "")).strip()
        longitude = str(data.get("longitude", "")).strip()
        pedido_id = data.get("pedido_id")

        if not latitude or not longitude:
            return jsonify({"ok": False, "mensagem": "Latitude e longitude são obrigatórias."}), 400

        pedido = None
        farmacia_id = None

        if pedido_id:
            pedido = Pedido.query.get(pedido_id)

            if not pedido:
                return jsonify({"ok": False, "mensagem": "Pedido não encontrado."}), 404

            if pedido.entregador_id != entregador.id:
                return jsonify({"ok": False, "mensagem": "Pedido não pertence a este entregador."}), 403

            if pedido.farmacia_id not in entregador.farmacias_ids:
                return jsonify({"ok": False, "mensagem": "Localização não permitida para esta farmácia."}), 403

            farmacia_id = pedido.farmacia_id
            session["entregador_farmacia_id"] = farmacia_id
        else:
            farmacia_id = session.get("entregador_farmacia_id")

            if not farmacia_id or farmacia_id not in entregador.farmacias_ids:
                return jsonify({"ok": False, "mensagem": "Farmácia da localização não definida."}), 400

        nova = Localizacao(
            farmacia_id=farmacia_id,
            entregador_id=entregador.id,
            pedido_id=pedido.id if pedido else None,
            latitude=latitude,
            longitude=longitude
        )
        db.session.add(nova)
        db.session.commit()

        return jsonify({"ok": True, "mensagem": "Localização enviada com sucesso."})

    @app.route("/api/mapa/entregadores")
    @login_required
    def mapa_entregadores():
        if current_user.is_master:
            return jsonify([])

        ids_usuario = farmacias_ids_do_usuario()
        if not ids_usuario:
            return jsonify([])

        farmacia_id_ativa = session.get("farmacia_ativa_id")

        if farmacia_id_ativa and farmacia_id_ativa in ids_usuario:
            ids_consulta = [farmacia_id_ativa]
        else:
            ids_consulta = ids_usuario

        entregadores_lista = Entregador.query.filter(
            Entregador.farmacia_id.in_(ids_consulta),
            Entregador.ativo.is_(True)
        ).all()

        resultado = []
        agora = datetime.now()
        limite_online = agora - timedelta(minutes=2)

        for e in entregadores_lista:
            ultima = Localizacao.query.filter_by(
                farmacia_id=e.farmacia_id,
                entregador_id=e.id
            ).order_by(Localizacao.id.desc()).first()

            if not ultima:
                continue

            try:
                lat = float(ultima.latitude)
                lng = float(ultima.longitude)
            except (TypeError, ValueError):
                continue

            data_ultima = ultima.data_hora
            if data_ultima is not None and getattr(data_ultima, "tzinfo", None) is not None:
                data_ultima = data_ultima.replace(tzinfo=None)

            online = data_ultima >= limite_online if data_ultima else False

            pedido = None
            cliente = None
            if ultima.pedido_id:
                pedido = db.session.get(Pedido, ultima.pedido_id)
                if pedido:
                    cliente = pedido.cliente

            farmacia = db.session.get(Farmacia, e.farmacia_id)

            cor_marcador = "#22c55e"
            if farmacia:
                cores = [
                    "#22c55e", "#3b82f6", "#f59e0b", "#ef4444",
                    "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16"
                ]
                cor_marcador = cores[farmacia.id % len(cores)]

            resultado.append({
                "id": e.id,
                "nome": e.nome,
                "telefone": e.telefone,
                "farmacia_id": e.farmacia_id,
                "farmacia_nome": farmacia.nome if farmacia else None,
                "latitude": lat,
                "longitude": lng,
                "data_hora": ultima.data_hora.strftime("%d/%m/%Y %H:%M:%S") if ultima.data_hora else "",
                "online": online,
                "pedido_id": ultima.pedido_id,
                "pedido_status": pedido.status if pedido else None,
                "cliente_nome": cliente.nome if cliente else None,
                "cliente_endereco": cliente.endereco if cliente else None,
                "cor": cor_marcador
            })

        return jsonify(resultado)
    # =========================
    # RASTREIO
    # =========================
    @app.route("/rastreio/<codigo>")
    def rastreio_cliente(codigo):
        pedido = Pedido.query.filter_by(codigo_rastreio=codigo).first_or_404()
        return render_template("rastreio.html", pedido=pedido)

    @app.route("/api/rastreio/<codigo>")
    def api_rastreio_cliente(codigo):
        pedido = Pedido.query.filter_by(codigo_rastreio=codigo).first_or_404()

        ultima = None
        if pedido.entregador_id:
            ultima = Localizacao.query.filter_by(
                farmacia_id=pedido.farmacia_id,
                entregador_id=pedido.entregador_id,
                pedido_id=pedido.id
            ).order_by(Localizacao.id.desc()).first()

        tempo_estimado_min = None

        if pedido.status == "saiu_entrega" and ultima and ultima.data_hora:
            agora = datetime.now()
            data_ref = ultima.data_hora

            if getattr(data_ref, "tzinfo", None) is not None:
                data_ref = data_ref.replace(tzinfo=None)

            diff_min = int((agora - data_ref).total_seconds() / 60)

            if diff_min <= 2:
                tempo_estimado_min = 10
            elif diff_min <= 5:
                tempo_estimado_min = 7
            elif diff_min <= 10:
                tempo_estimado_min = 4
            else:
                tempo_estimado_min = 2

        resposta = {
            "pedido_id": pedido.id,
            "codigo_rastreio": pedido.codigo_rastreio,
            "cliente": pedido.cliente.nome,
            "endereco": pedido.cliente.endereco,
            "status": pedido.status,
            "farmacia": pedido.farmacia.nome if pedido.farmacia else None,
            "entregador": pedido.entregador.nome if pedido.entregador else None,
            "latitude": ultima.latitude if ultima else None,
            "longitude": ultima.longitude if ultima else None,
            "data_hora": ultima.data_hora.strftime("%d/%m/%Y %H:%M:%S") if ultima and ultima.data_hora else None,
            "tempo_estimado_min": tempo_estimado_min
        }

        return jsonify(resposta)

    @app.route("/pedido/<int:pedido_id>/whatsapp-cliente")
    @login_required
    def whatsapp_cliente(pedido_id):
        pedido = Pedido.query.get_or_404(pedido_id)

        if not validar_acesso_farmacia(pedido.farmacia_id):
            abort(403)

        link_rastreio = url_for("rastreio_cliente", codigo=pedido.codigo_rastreio, _external=True)
        mensagem = (
            f"Olá, {pedido.cliente.nome}! "
            f"Seu pedido #{pedido.id} está em acompanhamento. "
            f"Acesse seu rastreio aqui: {link_rastreio}"
        )

        return redirect(link_whatsapp(pedido.cliente.telefone, mensagem))

    # =========================
    # RELATÓRIOS - SEM VALOR
    # =========================
    @app.route("/relatorios")
    @login_required
    def relatorios():
        if current_user.is_master:
            flash("Área disponível apenas para usuários de farmácia.", "warning")
            return redirect(url_for("dashboard"))

        farmacia_id = farmacia_ativa_id()
        if not farmacia_id:
            flash("Nenhuma farmácia ativa selecionada.", "warning")
            return redirect(url_for("dashboard"))

        inicio = request.args.get("inicio", "").strip()
        fim = request.args.get("fim", "").strip()

        query = Pedido.query.filter_by(farmacia_id=farmacia_id)

        if inicio:
            try:
                data_inicio = datetime.strptime(inicio, "%Y-%m-%d")
                query = query.filter(Pedido.criado_em >= data_inicio)
            except ValueError:
                flash("Data inicial inválida.", "danger")
                return redirect(url_for("relatorios"))

        if fim:
            try:
                data_fim = datetime.strptime(fim, "%Y-%m-%d") + timedelta(days=1)
                query = query.filter(Pedido.criado_em < data_fim)
            except ValueError:
                flash("Data final inválida.", "danger")
                return redirect(url_for("relatorios"))

        pedidos = query.order_by(Pedido.criado_em.desc()).all()

        total_pedidos = len(pedidos)
        total_entregues = sum(1 for p in pedidos if p.status == "entregue")
        total_recebidos = sum(1 for p in pedidos if p.status == "recebido")
        total_separacao = sum(1 for p in pedidos if p.status == "separacao")
        total_saiu_entrega = sum(1 for p in pedidos if p.status == "saiu_entrega")

        return render_template(
            "relatorios.html",
            pedidos=pedidos,
            total_pedidos=total_pedidos,
            total_entregues=total_entregues,
            total_recebidos=total_recebidos,
            total_separacao=total_separacao,
            total_saiu_entrega=total_saiu_entrega,
            inicio=inicio,
            fim=fim
        )

    @app.route("/relatorios/pdf")
    @login_required
    def relatorios_pdf():
        if current_user.is_master:
            return redirect(url_for("dashboard"))

        farmacia_id = farmacia_ativa_id()
        if not farmacia_id:
            return redirect(url_for("dashboard"))

        inicio = request.args.get("inicio", "").strip()
        fim = request.args.get("fim", "").strip()

        query = Pedido.query.filter_by(farmacia_id=farmacia_id)

        if inicio:
            try:
                data_inicio = datetime.strptime(inicio, "%Y-%m-%d")
                query = query.filter(Pedido.criado_em >= data_inicio)
            except ValueError:
                return redirect(url_for("relatorios"))

        if fim:
            try:
                data_fim = datetime.strptime(fim, "%Y-%m-%d") + timedelta(days=1)
                query = query.filter(Pedido.criado_em < data_fim)
            except ValueError:
                return redirect(url_for("relatorios"))

        pedidos = query.order_by(Pedido.criado_em.desc()).all()

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20,
            leftMargin=20,
            topMargin=24,
            bottomMargin=20
        )

        styles = getSampleStyleSheet()
        elementos = []

        titulo_style = ParagraphStyle(
            "TituloRelatorio",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#0f172a"),
            spaceAfter=8
        )

        subtitulo_style = ParagraphStyle(
            "SubtituloRelatorio",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#475569"),
            spaceAfter=10
        )

        texto_style = ParagraphStyle(
            "TextoRelatorio",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#111827")
        )

        farmacia = farmacia_do_usuario_logado()
        nome_farmacia = farmacia.nome if farmacia else "Farmácia"

        periodo_texto = "Período completo"
        if inicio and fim:
            periodo_texto = f"Período: {datetime.strptime(inicio, '%Y-%m-%d').strftime('%d/%m/%Y')} até {datetime.strptime(fim, '%Y-%m-%d').strftime('%d/%m/%Y')}"
        elif inicio:
            periodo_texto = f"Período: a partir de {datetime.strptime(inicio, '%Y-%m-%d').strftime('%d/%m/%Y')}"
        elif fim:
            periodo_texto = f"Período: até {datetime.strptime(fim, '%Y-%m-%d').strftime('%d/%m/%Y')}"

        total_pedidos = len(pedidos)
        total_entregues = sum(1 for p in pedidos if p.status == "entregue")
        total_recebidos = sum(1 for p in pedidos if p.status == "recebido")
        total_separacao = sum(1 for p in pedidos if p.status == "separacao")
        total_saiu_entrega = sum(1 for p in pedidos if p.status == "saiu_entrega")

        elementos.append(Paragraph(f"Relatório de Pedidos - {nome_farmacia}", titulo_style))
        elementos.append(Paragraph(periodo_texto, subtitulo_style))
        elementos.append(Paragraph(
            f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            subtitulo_style
        ))
        elementos.append(Spacer(1, 10))

        resumo = [
            ["Indicador", "Quantidade"],
            ["Total de pedidos", str(total_pedidos)],
            ["Recebidos", str(total_recebidos)],
            ["Em separação", str(total_separacao)],
            ["Saiu para entrega", str(total_saiu_entrega)],
            ["Entregues", str(total_entregues)],
        ]

        tabela_resumo = Table(resumo, colWidths=[260, 180])
        tabela_resumo.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d4ed8")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("ALIGN", (1, 1), (1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
        ]))
        elementos.append(tabela_resumo)
        elementos.append(Spacer(1, 16))

        dados = [[
            "Pedido", "Cliente", "Entregador", "Status",
            "Criado em", "Saiu entrega", "Entregue em"
        ]]

        for p in pedidos:
            cliente_nome = p.cliente.nome if p.cliente else "-"
            entregador_nome = p.entregador.nome if p.entregador else "-"
            status_formatado = (p.status or "-").replace("_", " ").upper()

            criado_em = p.criado_em.strftime("%d/%m/%Y %H:%M") if p.criado_em else "-"
            saiu_em = p.saiu_entrega_em.strftime("%d/%m/%Y %H:%M") if p.saiu_entrega_em else "-"
            entregue_em = p.entregue_em.strftime("%d/%m/%Y %H:%M") if p.entregue_em else "-"

            dados.append([
                f"#{p.id}",
                cliente_nome,
                entregador_nome,
                status_formatado,
                criado_em,
                saiu_em,
                entregue_em
            ])

        tabela = Table(
            dados,
            repeatRows=1,
            colWidths=[42, 105, 90, 78, 72, 72, 72]
        )
        tabela.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]))

        elementos.append(tabela)

        doc.build(elementos)
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name="relatorio_pedidos_farmacontrol.pdf",
            mimetype="application/pdf"
        )

    @app.route("/manifest.json")
    def manifest():
        return app.send_static_file("manifest.json")

    @app.route("/sw.js")
    def service_worker():
        return app.send_static_file("sw.js")


    @app.route("/privacy")
    def privacy():
        return """
        <h1>Política de Privacidade</h1>
        <p>O FarmaControl coleta apenas dados necessários para operação de pedidos e entregas.</p>
        <p>Não compartilhamos dados com terceiros.</p>
        <p>Os dados são utilizados apenas para funcionamento do sistema.</p>
        """


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)