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
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

from extensions import db, login_manager
from models import (
    Farmacia, User, Cliente, Entregador, Pedido, Localizacao,
    WhatsAppConfig, WhatsAppLog
)


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

    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"

    with app.app_context():
        db.create_all()
        criar_admin_master_padrao()
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


def user_farmacia_id():
    if current_user.is_master:
        return None
    return current_user.farmacia_id


def validar_acesso_farmacia(farmacia_id):
    if current_user.is_master:
        return True
    return current_user.farmacia_id == farmacia_id


def cliente_query():
    if current_user.is_master:
        return Cliente.query
    return Cliente.query.filter_by(farmacia_id=current_user.farmacia_id)


def entregador_query():
    if current_user.is_master:
        return Entregador.query
    return Entregador.query.filter_by(farmacia_id=current_user.farmacia_id)


def pedido_query():
    if current_user.is_master:
        return Pedido.query
    return Pedido.query.filter_by(farmacia_id=current_user.farmacia_id)


def farmacia_do_usuario_logado():
    if current_user.is_master or not current_user.farmacia_id:
        return None
    return db.session.get(Farmacia, current_user.farmacia_id)


def link_google_maps(endereco: str) -> str:
    destino = quote_plus(endereco or "")
    return f"https://www.google.com/maps/dir/?api=1&destination={destino}"


def link_waze(endereco: str) -> str:
    destino = quote_plus(endereco or "")
    return f"https://waze.com/ul?q={destino}&navigate=yes"


def link_whatsapp(numero: str, mensagem: str) -> str:
    numero_limpo = "".join(ch for ch in (numero or "") if ch.isdigit())
    texto = quote_plus(mensagem)
    return f"https://wa.me/{numero_limpo}?text={texto}"


def numero_whatsapp_formatado(numero: str) -> str:
    return "".join(ch for ch in (numero or "") if ch.isdigit())


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

    mensagem = (
        f"Olá, {pedido.cliente.nome}! "
        f"Recebemos seu pedido #{pedido.id} e já estamos preparando tudo."
    )

    enviar_texto_whatsapp(
        numero=pedido.cliente.telefone,
        mensagem=mensagem,
        farmacia_id=pedido.farmacia_id,
        pedido_id=pedido.id,
        tipo="pedido_recebido"
    )


def disparar_whatsapp_saiu_entrega(pedido):
    cfg = obter_config_whatsapp(pedido.farmacia_id)
    if not cfg or not cfg.ativo or not cfg.enviar_saiu_entrega:
        return

    link_rastreio = url_for(
        "rastreio_cliente",
        codigo=pedido.codigo_rastreio,
        _external=True
    )

    mensagem = (
        f"Olá, {pedido.cliente.nome}! "
        f"Seu pedido #{pedido.id} saiu para entrega. "
        f"Acompanhe aqui: {link_rastreio}"
    )

    enviar_texto_whatsapp(
        numero=pedido.cliente.telefone,
        mensagem=mensagem,
        farmacia_id=pedido.farmacia_id,
        pedido_id=pedido.id,
        tipo="saiu_entrega"
    )


def disparar_whatsapp_pedido_entregue(pedido):
    cfg = obter_config_whatsapp(pedido.farmacia_id)
    if not cfg or not cfg.ativo or not cfg.enviar_pedido_entregue:
        return

    mensagem = (
        f"Olá, {pedido.cliente.nome}! "
        f"Seu pedido #{pedido.id} foi entregue. "
        f"Obrigado pela preferência."
    )

    enviar_texto_whatsapp(
        numero=pedido.cliente.telefone,
        mensagem=mensagem,
        farmacia_id=pedido.farmacia_id,
        pedido_id=pedido.id,
        tipo="pedido_entregue"
    )


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
                if not user.is_master and not user.farmacia_id:
                    flash("Usuário sem farmácia vinculada.", "danger")
                    return redirect(url_for("login"))

                if not user.is_master:
                    farmacia = db.session.get(Farmacia, user.farmacia_id)
                    if not farmacia or not farmacia.ativo or farmacia.status != "ativa":
                        flash("Farmácia inativa ou bloqueada.", "danger")
                        return redirect(url_for("login"))

                login_user(user)
                flash("Login realizado com sucesso.", "success")
                return redirect(url_for("dashboard"))

            flash("E-mail ou senha inválidos.", "danger")

        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        session.pop("entregador_id", None)
        session.pop("entregador_farmacia_id", None)
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
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

        total_clientes = Cliente.query.filter_by(farmacia_id=current_user.farmacia_id).count()
        total_entregadores = Entregador.query.filter_by(farmacia_id=current_user.farmacia_id).count()
        total_pedidos = Pedido.query.filter_by(farmacia_id=current_user.farmacia_id).count()

        pedidos_recebidos = Pedido.query.filter_by(
            farmacia_id=current_user.farmacia_id, status="recebido"
        ).count()
        pedidos_separacao = Pedido.query.filter_by(
            farmacia_id=current_user.farmacia_id, status="separacao"
        ).count()
        pedidos_entrega = Pedido.query.filter_by(
            farmacia_id=current_user.farmacia_id, status="saiu_entrega"
        ).count()
        pedidos_entregues = Pedido.query.filter_by(
            farmacia_id=current_user.farmacia_id, status="entregue"
        ).count()

        ultimos_pedidos = Pedido.query.filter_by(
            farmacia_id=current_user.farmacia_id
        ).order_by(Pedido.id.desc()).limit(10).all()

        farmacia = farmacia_do_usuario_logado()

        return render_template(
            "dashboard.html",
            farmacia=farmacia,
            total_clientes=total_clientes,
            total_entregadores=total_entregadores,
            total_pedidos=total_pedidos,
            pedidos_recebidos=pedidos_recebidos,
            pedidos_separacao=pedidos_separacao,
            pedidos_entrega=pedidos_entrega,
            pedidos_entregues=pedidos_entregues,
            ultimos_pedidos=ultimos_pedidos
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
            farmacia_id = request.form.get("farmacia_id", "").strip()
            perfil = request.form.get("perfil", "admin").strip()

            if not nome or not email or not senha or not farmacia_id:
                flash("Preencha nome, e-mail, senha e farmácia.", "warning")
                return redirect(url_for("master_usuarios"))

            if User.query.filter_by(email=email).first():
                flash("Já existe usuário com esse e-mail.", "danger")
                return redirect(url_for("master_usuarios"))

            farmacia = db.session.get(Farmacia, int(farmacia_id))
            if not farmacia:
                flash("Farmácia inválida.", "danger")
                return redirect(url_for("master_usuarios"))

            novo = User(
                nome=nome,
                email=email,
                perfil=perfil if perfil in ["admin"] else "admin",
                farmacia_id=farmacia.id,
                ativo=True
            )
            novo.set_password(senha)

            db.session.add(novo)
            db.session.commit()

            flash("Usuário da farmácia cadastrado com sucesso.", "success")
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

        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            telefone = request.form.get("telefone", "").strip()
            endereco = request.form.get("endereco", "").strip()

            if not nome or not telefone or not endereco:
                flash("Preencha todos os campos do cliente.", "warning")
                return redirect(url_for("clientes"))

            novo = Cliente(
                farmacia_id=current_user.farmacia_id,
                nome=nome,
                telefone=telefone,
                endereco=endereco
            )
            db.session.add(novo)
            db.session.commit()
            flash("Cliente cadastrado com sucesso.", "success")
            return redirect(url_for("clientes"))

        lista = Cliente.query.filter_by(
            farmacia_id=current_user.farmacia_id
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

        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            telefone = request.form.get("telefone", "").strip()
            senha = request.form.get("senha", "").strip()

            if not nome or not telefone or not senha:
                flash("Preencha todos os campos do entregador.", "warning")
                return redirect(url_for("entregadores"))

            existe = Entregador.query.filter_by(
                farmacia_id=current_user.farmacia_id,
                telefone=telefone
            ).first()

            if existe:
                flash("Já existe entregador com esse telefone nesta farmácia.", "danger")
                return redirect(url_for("entregadores"))

            novo = Entregador(
                farmacia_id=current_user.farmacia_id,
                nome=nome,
                telefone=telefone,
                ativo=True
            )
            novo.set_password(senha)

            db.session.add(novo)
            db.session.commit()
            flash("Entregador cadastrado com sucesso.", "success")
            return redirect(url_for("entregadores"))

        lista = Entregador.query.filter_by(
            farmacia_id=current_user.farmacia_id
        ).order_by(Entregador.id.desc()).all()

        return render_template("entregadores.html", entregadores=lista)

    # =========================
    # PEDIDOS
    # =========================
    @app.route("/api/pedidos/ultimo")
    @login_required
    def ultimo_pedido():
        if current_user.is_master:
            ultimo = Pedido.query.order_by(Pedido.id.desc()).first()
        else:
            ultimo = Pedido.query.filter_by(
                farmacia_id=current_user.farmacia_id
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

        if request.method == "POST":
            cliente_id = request.form.get("cliente_id")
            entregador_id = request.form.get("entregador_id")
            status = request.form.get("status", "recebido").strip()

            if not cliente_id:
                flash("Cliente é obrigatório.", "warning")
                return redirect(url_for("pedidos"))

            cliente = Cliente.query.filter_by(
                id=int(cliente_id),
                farmacia_id=current_user.farmacia_id
            ).first()

            if not cliente:
                flash("Cliente inválido para esta farmácia.", "danger")
                return redirect(url_for("pedidos"))

            entregador = None
            if entregador_id:
                entregador = Entregador.query.filter_by(
                    id=int(entregador_id),
                    farmacia_id=current_user.farmacia_id,
                    ativo=True
                ).first()

                if not entregador:
                    flash("Entregador inválido para esta farmácia.", "danger")
                    return redirect(url_for("pedidos"))

            codigo = gerar_codigo_rastreio()
            while Pedido.query.filter_by(codigo_rastreio=codigo).first():
                codigo = gerar_codigo_rastreio()

            novo = Pedido(
                farmacia_id=current_user.farmacia_id,
                cliente_id=cliente.id,
                entregador_id=entregador.id if entregador else None,
                status=status,
                codigo_rastreio=codigo
            )

            if status == "saiu_entrega":
                novo.saiu_entrega_em = agora_brasil()
            elif status == "entregue":
                novo.entregue_em = agora_brasil()

            db.session.add(novo)
            db.session.commit()

            if novo.status == "recebido":
                disparar_whatsapp_pedido_recebido(novo)

            if novo.status == "saiu_entrega":
                disparar_whatsapp_saiu_entrega(novo)

            if novo.status == "entregue":
                disparar_whatsapp_pedido_entregue(novo)

            flash("Pedido cadastrado com sucesso.", "success")
            return redirect(url_for("pedidos"))

        lista = Pedido.query.filter_by(
            farmacia_id=current_user.farmacia_id
        ).order_by(Pedido.id.desc()).all()

        clientes_lista = Cliente.query.filter_by(
            farmacia_id=current_user.farmacia_id
        ).order_by(Cliente.nome.asc()).all()

        entregadores_lista = Entregador.query.filter_by(
            farmacia_id=current_user.farmacia_id,
            ativo=True
        ).order_by(Entregador.nome.asc()).all()

        return render_template(
            "pedidos.html",
            pedidos=lista,
            clientes=clientes_lista,
            entregadores=entregadores_lista
        )

    @app.route("/pedido/<int:pedido_id>/status", methods=["POST"])
    @login_required
    def atualizar_status_pedido(pedido_id):
        pedido = Pedido.query.get_or_404(pedido_id)

        if not current_user.is_master and pedido.farmacia_id != current_user.farmacia_id:
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

        if status_anterior != novo_status:
            if novo_status == "saiu_entrega":
                disparar_whatsapp_saiu_entrega(pedido)
            elif novo_status == "entregue":
                disparar_whatsapp_pedido_entregue(pedido)

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

        garantir_whatsapp_config(current_user.farmacia_id)
        cfg = obter_config_whatsapp(current_user.farmacia_id)

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
            farmacia_id=current_user.farmacia_id
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

        numero = request.form.get("numero", "").strip()
        mensagem = request.form.get("mensagem", "").strip()

        if not numero or not mensagem:
            flash("Informe número e mensagem para o teste.", "warning")
            return redirect(url_for("whatsapp_config"))

        resultado = enviar_texto_whatsapp(
            numero=numero,
            mensagem=mensagem,
            farmacia_id=current_user.farmacia_id,
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

    # =========================
    # APP DO ENTREGADOR
    # =========================
    @app.route("/entregador/login", methods=["GET", "POST"])
    def entregador_login():
        farmacias = Farmacia.query.filter_by(ativo=True, status="ativa").order_by(Farmacia.nome.asc()).all()

        if request.method == "POST":
            farmacia_id = request.form.get("farmacia_id", "").strip()
            telefone = request.form.get("telefone", "").strip()
            senha = request.form.get("senha", "").strip()

            if not farmacia_id or not telefone or not senha:
                flash("Preencha farmácia, telefone e senha.", "warning")
                return redirect(url_for("entregador_login"))

            entregador = Entregador.query.filter_by(
                farmacia_id=int(farmacia_id),
                telefone=telefone,
                ativo=True
            ).first()

            if entregador and entregador.check_password(senha):
                session["entregador_id"] = entregador.id
                session["entregador_farmacia_id"] = entregador.farmacia_id
                flash("Login realizado com sucesso.", "success")
                return redirect(url_for("entregador_app"))

            flash("Dados inválidos.", "danger")

        return render_template("entregador_login.html", farmacias=farmacias)

    @app.route("/entregador/logout")
    def entregador_logout():
        session.pop("entregador_id", None)
        session.pop("entregador_farmacia_id", None)
        flash("Você saiu da área do entregador.", "info")
        return redirect(url_for("entregador_login"))

    @app.route("/entregador/app")
    def entregador_app():
        entregador_id = session.get("entregador_id")
        farmacia_id = session.get("entregador_farmacia_id")

        if not entregador_id or not farmacia_id:
            flash("Faça login como entregador.", "warning")
            return redirect(url_for("entregador_login"))

        entregador = Entregador.query.filter_by(
            id=entregador_id,
            farmacia_id=farmacia_id
        ).first_or_404()

        pedidos = Pedido.query.filter(
            Pedido.farmacia_id == farmacia_id,
            Pedido.entregador_id == entregador.id,
            Pedido.status.in_(["recebido", "separacao", "saiu_entrega"])
        ).order_by(Pedido.id.desc()).all()

        return render_template(
            "entregador_app.html",
            entregador=entregador,
            pedidos=pedidos
        )

    @app.route("/pedido/<int:pedido_id>/iniciar-entrega", methods=["POST"])
    def iniciar_entrega(pedido_id):
        entregador_id = session.get("entregador_id")
        farmacia_id = session.get("entregador_farmacia_id")

        if not entregador_id or not farmacia_id:
            return jsonify({"ok": False, "mensagem": "Entregador não autenticado."}), 401

        pedido = Pedido.query.filter_by(
            id=pedido_id,
            farmacia_id=farmacia_id
        ).first_or_404()

        if pedido.entregador_id != entregador_id:
            return jsonify({"ok": False, "mensagem": "Pedido não pertence a este entregador."}), 403

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
        farmacia_id = session.get("entregador_farmacia_id")

        if not entregador_id or not farmacia_id:
            return jsonify({"ok": False, "mensagem": "Entregador não autenticado."}), 401

        pedido = Pedido.query.filter_by(
            id=pedido_id,
            farmacia_id=farmacia_id
        ).first_or_404()

        if pedido.entregador_id != entregador_id:
            return jsonify({"ok": False, "mensagem": "Pedido não pertence a este entregador."}), 403

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
        farmacia_id = session.get("entregador_farmacia_id")

        if not entregador_id or not farmacia_id:
            return jsonify({"ok": False, "mensagem": "Entregador não autenticado."}), 401

        entregador = Entregador.query.filter_by(
            id=entregador_id,
            farmacia_id=farmacia_id
        ).first_or_404()

        data = request.get_json(silent=True) or {}
        latitude = str(data.get("latitude", "")).strip()
        longitude = str(data.get("longitude", "")).strip()
        pedido_id = data.get("pedido_id")

        if not latitude or not longitude:
            return jsonify({"ok": False, "mensagem": "Latitude e longitude são obrigatórias."}), 400

        pedido = None
        if pedido_id:
            pedido = Pedido.query.filter_by(
                id=pedido_id,
                farmacia_id=farmacia_id
            ).first()

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

        entregadores_lista = Entregador.query.filter_by(
            farmacia_id=current_user.farmacia_id,
            ativo=True
        ).all()

        resultado = []
        agora = datetime.now()
        limite_online = agora - timedelta(minutes=2)

        for e in entregadores_lista:
            ultima = Localizacao.query.filter_by(
                farmacia_id=current_user.farmacia_id,
                entregador_id=e.id
            ).order_by(Localizacao.id.desc()).first()

            if ultima:
                data_ultima = ultima.data_hora
                if data_ultima is not None and getattr(data_ultima, "tzinfo", None) is not None:
                    data_ultima = data_ultima.replace(tzinfo=None)

                online = data_ultima >= limite_online if data_ultima else False

                pedido = None
                if ultima.pedido_id:
                    pedido = db.session.get(Pedido, ultima.pedido_id)

                resultado.append({
                    "id": e.id,
                    "nome": e.nome,
                    "telefone": e.telefone,
                    "latitude": ultima.latitude,
                    "longitude": ultima.longitude,
                    "data_hora": ultima.data_hora.strftime("%d/%m/%Y %H:%M:%S") if ultima.data_hora else "",
                    "online": online,
                    "pedido_id": ultima.pedido_id,
                    "pedido_status": pedido.status if pedido else None
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

        if not current_user.is_master and pedido.farmacia_id != current_user.farmacia_id:
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

        inicio = request.args.get("inicio", "").strip()
        fim = request.args.get("fim", "").strip()

        query = Pedido.query.filter_by(farmacia_id=current_user.farmacia_id)

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

        inicio = request.args.get("inicio", "").strip()
        fim = request.args.get("fim", "").strip()

        query = Pedido.query.filter_by(farmacia_id=current_user.farmacia_id)

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
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        elementos = []

        farmacia = farmacia_do_usuario_logado()
        nome_farmacia = farmacia.nome if farmacia else "Farmácia"

        elementos.append(Paragraph(f"Relatório de Pedidos - {nome_farmacia}", styles["Title"]))
        elementos.append(Spacer(1, 12))

        total_pedidos = len(pedidos)
        total_entregues = sum(1 for p in pedidos if p.status == "entregue")
        total_recebidos = sum(1 for p in pedidos if p.status == "recebido")
        total_separacao = sum(1 for p in pedidos if p.status == "separacao")
        total_saiu_entrega = sum(1 for p in pedidos if p.status == "saiu_entrega")

        resumo = [
            ["Total de pedidos", str(total_pedidos)],
            ["Recebidos", str(total_recebidos)],
            ["Em separação", str(total_separacao)],
            ["Saiu para entrega", str(total_saiu_entrega)],
            ["Entregues", str(total_entregues)],
        ]

        tabela_resumo = Table(resumo, colWidths=[180, 180])
        tabela_resumo.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ]))

        elementos.append(tabela_resumo)
        elementos.append(Spacer(1, 18))

        dados = [["Pedido", "Cliente", "Entregador", "Status", "Data"]]

        for p in pedidos:
            dados.append([
                f"#{p.id}",
                p.cliente.nome if p.cliente else "-",
                p.entregador.nome if p.entregador else "-",
                p.status,
                p.criado_em.strftime("%d/%m/%Y") if p.criado_em else "-"
            ])

        tabela = Table(dados, repeatRows=1)
        tabela.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
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


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)