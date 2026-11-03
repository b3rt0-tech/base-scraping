from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from scripts.basecnpj.models import MAX_DECISORES, CNPJDetalhe, Socio
from scripts.pipeline.export import build_lead_json
from scripts.pipeline.score import compute_score
from scripts.pipeline.validate import ValidacaoResult
from scripts.pipeline_cli import app

from .conftest import CNPJ_PADRAO, make_cnpj_raw


def _socio(nome, qualificacao="Sócio", *, doc="***.111.222-**", tipo="1", entrada="2020-01-01", **extra):
    return {
        "nome_razao": nome,
        "tipo_socio": tipo,
        "documento_mascarado": doc,
        "data_entrada": entrada,
        "qualificacao_codigo": "00",
        "qualificacao_descricao": qualificacao,
        "faixa_etaria": "41 a 50 anos",
        "faixa_etaria_codigo": "4",
        "pais": None,
        "representante_nome": None,
        "representante_qualificacao": "Não informada",
        **extra,
    }


def _detalhe(socios, **overrides) -> CNPJDetalhe:
    return CNPJDetalhe.model_validate(make_cnpj_raw(socios=socios, **overrides))


def _nomes(detalhe: CNPJDetalhe) -> list[str]:
    return [d.nome for d in detalhe.decisores()]


# --- Socio: o que é lido e o que é descartado ----------------------------------


def test_socio_descarta_cpf_e_faixa_etaria():
    socio = Socio.model_validate(_socio("ANA", "Sócio-Administrador"))
    assert socio.pessoa_fisica
    assert set(socio.model_dump()) == {"nome", "qualificacao", "entrada", "pessoa_fisica"}
    assert "***" not in json.dumps(socio.model_dump()) and "anos" not in json.dumps(socio.model_dump())


def test_cnpj_detalhe_persistido_no_checkpoint_nao_guarda_dado_pessoal():
    detalhe = _detalhe([_socio("ANA", "Sócio-Administrador")])
    persistido = json.dumps(detalhe.model_dump())
    assert "documento_mascarado" not in persistido and "faixa_etaria" not in persistido and "***." not in persistido
    # e o que foi persistido volta a virar o mesmo modelo (retomada via --raw do SQLite)
    assert [d.nome for d in CNPJDetalhe.model_validate(detalhe.model_dump()).decisores()] == ["ANA"]


@pytest.mark.parametrize(
    "doc, tipo, pessoa_fisica",
    [
        ("***.123.456-**", "1", True),  # forma observada na API real
        ("***.123.456-**", "9", True),  # o documento manda mais que o código
        ("**.123.456/0001-**", "1", False),  # CNPJ mascarado nunca é pessoa física
        ("—", "3", False),  # estrangeiro
        ("", "1", True),  # sem documento: cai no código observado
        ("", "3", False),
        ("", None, False),
    ],
)
def test_pessoa_fisica_pelo_documento_mascarado(doc, tipo, pessoa_fisica):
    assert Socio.model_validate(_socio("X", doc=doc, tipo=tipo)).pessoa_fisica is pessoa_fisica


def test_nome_vindo_da_api_com_espaco_no_fim_e_normalizado():
    assert Socio.model_validate(_socio("JOAO CARDOSO CARMEZIM ", "Sócio-Administrador ")).nome == "JOAO CARDOSO CARMEZIM"
    assert _nomes(_detalhe([_socio("ANA  ", "Sócio"), _socio("ANA", "Diretor")])) == ["ANA"]  # e deduplica


# --- decisores(): quem aparece e em que ordem ----------------------------------


def test_prioriza_por_qualificacao():
    d = _detalhe(
        [
            _socio("SOCIA", "Sócio"),
            _socio("GERENTE", "Sócio-Gerente"),
            _socio("DIRETORA", "Diretor"),
            _socio("PRESIDENTE", "Presidente"),
            _socio("ADMIN", "Sócio-Administrador"),
        ]
    )
    assert _nomes(d) == ["ADMIN", "PRESIDENTE", "DIRETORA"]  # teto de 3


def test_desempata_por_data_de_entrada_mais_antiga():
    d = _detalhe([_socio("NOVO", entrada="2024-01-01"), _socio("ANTIGO", entrada="2001-05-05")])
    assert _nomes(d) == ["ANTIGO", "NOVO"]


def test_conselheiro_vem_depois_de_socio():
    d = _detalhe([_socio("CONSELHEIRO", "Conselheiro de Administração"), _socio("SOCIO", "Sócio")])
    assert _nomes(d) == ["SOCIO", "CONSELHEIRO"]


def test_qualificacao_com_acento_e_caixa_diferentes():
    assert _nomes(_detalhe([_socio("A", "SÓCIO-ADMINISTRADOR"), _socio("B", "Diretor")])) == ["A", "B"]


def test_mesma_pessoa_em_duas_qualificacoes_aparece_uma_vez():
    d = _detalhe([_socio("Maria José", "Diretor"), _socio("MARIA JOSE", "Presidente"), _socio("OUTRO", "Sócio")])
    assert _nomes(d) == ["MARIA JOSE", "OUTRO"]


def test_empresa_grande_respeita_o_teto():
    d = _detalhe([_socio(f"DIRETOR {i}", "Diretor") for i in range(41)])
    assert len(d.decisores()) == MAX_DECISORES == 3


def test_socio_pessoa_juridica_e_estrangeiro_nao_sao_decisores():
    d = _detalhe(
        [
            _socio("HOLDING LTDA", "Sócio Pessoa Jurídica Domiciliado no Brasil", doc="**.123.456/0001-**", tipo="2"),
            _socio("READING CORP", "Sócio Pessoa Jurídica Domiciliado no Exterior", doc="—", tipo="3"),
        ]
    )
    assert d.decisores() == []


def test_empresa_sem_socios_e_nao_individual_fica_sem_decisor():
    assert _detalhe([]).decisores() == []


def test_payload_sem_a_chave_socios_nao_quebra():
    raw = make_cnpj_raw()
    raw.pop("socios")
    assert CNPJDetalhe.model_validate(raw).decisores() == []


# --- empresário individual: o titular é a empresa --------------------------------


def test_empresario_individual_vira_decisor_titular():
    d = _detalhe(
        [],
        razao_social="ALINE GOMES BERNARDO",
        natureza_juridica_descricao="Empresário (Individual)",
        matriz={"data_inicio_atividade": "2019-02-03"},
    )
    [decisor] = d.decisores()
    assert (decisor.nome, decisor.qualificacao, decisor.desde, decisor.origem) == (
        "ALINE GOMES BERNARDO",
        "Titular (empresário individual)",
        "2019-02-03",
        "razao_social_titular",
    )


def test_empresario_individual_com_quadro_usa_o_quadro():
    d = _detalhe(
        [_socio("TITULAR REAL", "Titular Pessoa Física")],
        razao_social="FIRMA DE FULANO",
        natureza_juridica_descricao="Empresário (Individual)",
    )
    assert [(x.nome, x.origem) for x in d.decisores()] == [("TITULAR REAL", "quadro_societario")]


def test_ltda_sem_socios_nao_ganha_titular_inventado():
    assert _detalhe([], natureza_juridica_descricao="Sociedade Empresária Limitada").decisores() == []


# --- saída: contrato, proveniência e LGPD ----------------------------------------


def _lead(detalhe: CNPJDetalhe) -> dict:
    validacao = ValidacaoResult(cnpj=CNPJ_PADRAO, status="validado", detalhe=detalhe, cnae_match="principal")
    return build_lead_json(validacao, compute_score(validacao), validado_em=1.0)


def test_lead_traz_decisores_com_proveniencia_e_sem_socios_cru():
    lead = _lead(_detalhe([_socio("ANA", "Sócio-Administrador", entrada="2015-03-10")]))
    assert lead["decisores"] == [
        {"nome": "ANA", "qualificacao": "Sócio-Administrador", "desde": "2015-03-10", "origem": "quadro_societario"}
    ]
    assert lead["proveniencia"]["decisores"] == {"fonte": "basecnpj", "rota_ou_url": "route=cnpj", "timestamp": 1.0}
    assert "socios" not in lead


def test_lead_sem_decisor_nao_tem_o_campo():
    lead = _lead(_detalhe([]))
    assert "decisores" not in lead and "decisores" not in lead["proveniencia"]


def test_nenhum_dado_pessoal_alem_do_nome_chega_a_saida_do_cli(api, tmp_path):
    cnpj_ok = make_cnpj_raw(socios=[_socio("ANA SOUZA", "Sócio-Administrador", doc="***.987.654-**")])
    api({"status": {"status": "ok"}, "cnpj": lambda req: __import__("httpx").Response(200, json={"ok": True, "data": cnpj_ok})})
    lista = tmp_path / "lista.json"
    lista.write_text(json.dumps([{"cnpj": CNPJ_PADRAO}]))

    resultado = CliRunner().invoke(app, ["validar-lista", "--arquivo", str(lista)])

    assert resultado.exit_code == 0
    assert json.loads(resultado.stdout)["leads"][0]["decisores"][0]["nome"] == "ANA SOUZA"
    for vazamento in ("***", "987.654", "faixa_etaria", "41 a 50 anos", "documento_mascarado"):
        assert vazamento not in resultado.stdout


@pytest.mark.parametrize(
    "nome, pj",
    [
        ("ALFA ASSESSORIA EMPRESARIAL E PARTICIPACOES LTDA", True),  # caso real: CNPJ mascarado como CPF
        ("FULANO PARTICIPAÇÕES S/A", True),
        ("ACME S.A.", True),
        ("ACME S.A", True),
        ("XYZ HOLDING", True),
        ("COMERCIAL BETA EIRELI", True),
        ("JOAO DA SILVA ME", True),
        ("MARIA DE SA", False),  # Sá é sobrenome
        ("ANA SAMPAIO", False),
        ("CARLOS MELO", False),
        ("CARLOS ALBERTO SOUZA", False),
    ],
)
def test_parece_pessoa_juridica(nome, pj):
    from scripts.basecnpj.models import parece_pessoa_juridica

    assert parece_pessoa_juridica(nome) is pj


def test_socio_empresa_com_documento_mascarado_como_cpf_nao_e_decisor():
    from scripts.basecnpj.models import CNPJDetalhe

    raw = make_cnpj_raw(
        socios=[
            {"nome_razao": "ALFA ASSESSORIA EMPRESARIAL E PARTICIPACOES LTDA", "tipo_socio": "1",
             "documento_mascarado": "***.***.*27-**", "qualificacao_descricao": "Sócio"},
            {"nome_razao": "CARLOS ALBERTO SOUZA", "tipo_socio": "1",
             "documento_mascarado": "***.***.*48-**", "qualificacao_descricao": "Administrador"},
        ]
    )
    detalhe = CNPJDetalhe.model_validate(raw)
    assert [d.nome for d in detalhe.decisores()] == ["CARLOS ALBERTO SOUZA"]
    # checkpoint antigo (pessoa_fisica=True gravado antes da correção) também é filtrado
    recarregado = CNPJDetalhe.model_validate(
        {**detalhe.model_dump(), "socios": [{**s.model_dump(), "pessoa_fisica": True} for s in detalhe.socios]}
    )
    assert [d.nome for d in recarregado.decisores()] == ["CARLOS ALBERTO SOUZA"]
