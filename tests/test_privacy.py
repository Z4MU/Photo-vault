"""Fase 9: PIN, código de recuperación, intentos, filtro de lo oculto y caché cifrado de miniaturas."""

import pytest

import database as db
import privacy
import services
import thumbnail_cache
from services import GalleryQuery
from tests.test_gallery_ui import make_photos


@pytest.fixture
def world(tmp_path):
    """6 fotos; 'privado' es oculta y la tienen f0 y f1; 'playa' (visible) la tienen f1 y f2."""
    ids = make_photos(tmp_path, 6)
    secret = services.create_tag("privado")
    playa = services.create_tag("playa")
    services.add_tag_by_id(ids[0], secret)
    services.add_tag_by_id(ids[1], secret)
    services.add_tag_by_id(ids[1], playa)
    services.add_tag_by_id(ids[2], playa)
    services.set_tag_hidden(secret, True)
    return ids, secret, playa


def plain_thumbs() -> list:
    return list(thumbnail_cache.CACHE_DIR.rglob("*.jpg")) if thumbnail_cache.CACHE_DIR.exists() else []


def private_thumbs() -> list:
    d = thumbnail_cache.PRIVATE_DIR
    return list(d.rglob("*.pvt")) if d.exists() else []


# ── PIN ───────────────────────────────────────────────────────────────────────


def test_crear_pin_bloquear_y_desbloquear(db_path):
    db.init_db()
    assert not privacy.has_pin() and not privacy.is_unlocked() and not privacy.hidden_locked()
    code = privacy.set_pin("1234")
    assert len(code) == 23 and code.count("-") == 3
    assert privacy.has_pin() and privacy.is_unlocked()
    # Lo guardado no contiene el PIN ni la clave en claro
    raw = db.get_setting(privacy.VAULT_SETTING, "") or ""
    assert "1234" not in raw
    privacy.lock()
    assert privacy.hidden_locked()
    assert privacy.unlock("4321") is False
    assert not privacy.is_unlocked()
    assert privacy.unlock("1234") is True
    with pytest.raises(privacy.PrivacyError):
        privacy.set_pin("5678")  # ya hay uno


def test_pin_muy_corto_o_con_espacios(db_path):
    db.init_db()
    assert privacy.pin_problem("123")
    assert privacy.pin_problem(" 1234")
    assert privacy.pin_problem("abcd") is None
    with pytest.raises(privacy.PrivacyError):
        privacy.set_pin("12")
    assert not privacy.has_pin()


def test_codigo_de_recuperacion_y_cambiar_pin(db_path):
    db.init_db()
    code = privacy.set_pin("1234")
    privacy.lock()
    # Sin guiones y en minúsculas también sirve
    assert privacy.unlock_with_recovery(code.replace("-", "").lower()) is True
    privacy.change_pin("nuevo-pin")
    privacy.lock()
    assert privacy.unlock("1234") is False
    assert privacy.unlock("nuevo-pin") is True
    # Código nuevo: el anterior deja de servir
    code2 = privacy.new_recovery_code()
    privacy.lock()
    assert privacy.unlock_with_recovery(code) is False
    assert privacy.unlock_with_recovery(code2) is True


def test_cambiar_pin_requiere_desbloquear(db_path):
    db.init_db()
    privacy.set_pin("1234")
    privacy.lock()
    with pytest.raises(privacy.PrivacyError):
        privacy.change_pin("5678")
    with pytest.raises(privacy.PrivacyError):
        privacy.remove_pin()


def test_demasiados_intentos_hay_que_esperar(db_path, monkeypatch):
    db.init_db()
    privacy.set_pin("1234")
    privacy.lock()
    now = [1_000_000.0]
    monkeypatch.setattr(privacy.time, "time", lambda: now[0])
    for _ in range(privacy.MAX_ATTEMPTS):
        assert privacy.unlock("0000") is False
    assert privacy.lockout_remaining() == privacy.LOCKOUT_SECONDS
    with pytest.raises(privacy.LockedOutError):
        privacy.unlock("1234")  # ni con el PIN correcto mientras dure la espera
    now[0] += privacy.LOCKOUT_SECONDS
    assert privacy.unlock("1234") is True
    assert privacy.lockout_remaining() == 0


def test_quitar_pin_borra_el_cache_cifrado(world):
    ids, _secret, _playa = world
    privacy.set_pin("1234")
    photo = services.get_photo(ids[0])
    assert photo is not None and thumbnail_cache.get_photo_thumbnail(photo)
    assert private_thumbs()
    privacy.remove_pin()
    assert not privacy.has_pin() and not privacy.is_unlocked()
    assert not private_thumbs()


def test_opciones_y_tecla_de_panico(db_path):
    db.init_db()
    assert privacy.get_options() == privacy.PrivacyOptions()
    privacy.set_options(privacy.PrivacyOptions(10, False, "Ctrl+Shift+P", "close"))
    assert privacy.get_options() == privacy.PrivacyOptions(10, False, "Ctrl+Shift+P", "close")
    db.set_setting(privacy.OPTIONS_SETTING, "{basura")
    assert privacy.get_options() == privacy.PrivacyOptions()
    assert privacy.panic_key_problem("F12") is None
    assert privacy.panic_key_problem("Ctrl+P") is None
    assert privacy.panic_key_problem("") is None  # sin modo pánico
    assert privacy.panic_key_problem("P")  # una letra sola se usa al etiquetar
    assert privacy.panic_key_problem("Left")
    assert privacy.panic_key_problem("F11")  # pantalla completa


# ── Qué se muestra ────────────────────────────────────────────────────────────


def test_galeria_oculta_lo_oculto_hasta_desbloquear(world):
    ids, secret, playa = world
    q = GalleryQuery()
    assert set(services.get_gallery_ids(q)) == set(ids[2:])
    # Sin PIN no hay forma de desbloquear: sigue oculto
    privacy.set_pin("1234")  # crear el PIN desbloquea
    assert set(services.get_gallery_ids(q)) == set(ids)
    assert set(services.get_gallery_ids(GalleryQuery(tag_ids=(secret,)))) == {ids[0], ids[1]}
    assert set(services.get_gallery_ids(GalleryQuery(tag_ids=(playa,)))) == {ids[1], ids[2]}
    privacy.lock()
    assert set(services.get_gallery_ids(q)) == set(ids[2:])
    assert services.get_gallery_ids(GalleryQuery(tag_ids=(secret,))) == []


def test_foto_sabe_si_esta_oculta(world):
    ids, _secret, _playa = world
    hidden = {p.id for p in db.get_photos_by_ids(ids) if p.hidden}
    assert hidden == {ids[0], ids[1]}
    assert {p.id for p in db.get_hidden_photos()} == hidden
    assert len(db.get_paths_with_mtime(hidden=True)) == 2
    assert len(db.get_paths_with_mtime(hidden=False)) == 4
    assert len(db.get_paths_with_mtime()) == 6


def test_nombres_de_etiquetas_ocultas_solo_con_pin_bloqueado(world):
    _ids, secret, _playa = world
    # Sin PIN (como antes de la fase 9) se ven: si no, no se podrían des-ocultar
    assert secret in {t.id for t in services.get_all_tags()}
    assert services.count_locked_hidden_tags() == 0
    privacy.set_pin("1234")
    privacy.lock()
    assert secret not in {t.id for t in services.get_all_tags()}
    assert all(t.id != secret for g in services.get_sidebar_tags().values() for t in g)
    assert services.count_locked_hidden_tags() == 1
    assert "privado" not in dict(services.get_stats().top_tags)
    privacy.unlock("1234")
    assert secret in {t.id for t in services.get_all_tags()}
    assert "privado" in dict(services.get_stats().top_tags)


def test_duplicados_ocultos_solo_desbloqueado(world, tmp_path):
    ids, _secret, _playa = world
    for pid in (ids[0], ids[3]):
        db.update_photo_md5(pid, "igual-oculta")
    for pid in (ids[4], ids[5]):
        db.update_photo_md5(pid, "igual-visible")
    assert [g.md5 for g in services.get_duplicate_groups()] == ["igual-visible"]
    privacy.set_pin("1234")
    assert {g.md5 for g in services.get_duplicate_groups()} == {"igual-oculta", "igual-visible"}


# ── Miniaturas ────────────────────────────────────────────────────────────────


def test_miniatura_de_foto_oculta_va_cifrada(world):
    ids, _secret, _playa = world
    hidden = services.get_photo(ids[0])
    visible = services.get_photo(ids[3])
    assert hidden is not None and visible is not None and hidden.hidden
    # Bloqueado: ni se genera ni se escribe nada
    assert thumbnail_cache.get_photo_thumbnail(hidden) is None
    assert not plain_thumbs() and not private_thumbs()
    assert thumbnail_cache.get_photo_thumbnail(visible)
    assert len(plain_thumbs()) == 1

    privacy.set_pin("1234")
    jpeg = thumbnail_cache.get_photo_thumbnail(hidden)
    assert jpeg and jpeg[:2] == b"\xff\xd8"
    assert len(plain_thumbs()) == 1  # nada nuevo sin cifrar
    (pvt,) = private_thumbs()
    blob = pvt.read_bytes()
    assert b"\xff\xd8" not in blob[:16] and b"JFIF" not in blob
    assert thumbnail_cache.is_photo_cached(hidden)
    # Bloquear y volver a desbloquear: se lee del caché (misma clave maestra)
    privacy.lock()
    assert thumbnail_cache.get_photo_thumbnail(hidden) is None
    assert not thumbnail_cache.is_photo_cached(hidden)
    privacy.unlock("1234")
    assert thumbnail_cache.get_photo_thumbnail(hidden) == jpeg
    assert len(private_thumbs()) == 1


def test_al_ocultar_se_borran_las_miniaturas_sin_cifrar(world):
    ids, secret, playa = world
    photos = db.get_photos_by_ids([ids[2], ids[3]])
    for p in photos:
        for size in (100, 200, 480):
            assert thumbnail_cache.get_photo_thumbnail(p, size)
    assert len(plain_thumbs()) == 6
    # Etiquetar con una etiqueta oculta → sus miniaturas sin cifrar se van
    services.add_tag_by_id(ids[3], secret)
    assert len(plain_thumbs()) == 3
    # Ocultar una etiqueta → las de sus fotos también
    services.set_tag_hidden(playa, True)
    assert plain_thumbs() == []


def test_al_abrir_se_borran_las_sin_cifrar_que_quedaron(world):
    ids, _secret, _playa = world
    # Miniatura sin cifrar de una foto oculta (de antes de la fase 9)
    thumbnail_cache.get_thumbnail(
        db.get_photos_by_ids([ids[0]])[0].path, mtime=db.get_photos_by_ids([ids[0]])[0].mtime
    )
    assert len(plain_thumbs()) == 1
    assert services.secure_hidden_thumbnails() == 1
    assert plain_thumbs() == []


def test_purgar_huerfanos_con_lo_oculto(world):
    ids, _secret, _playa = world
    privacy.set_pin("1234")
    for p in db.get_photos_by_ids(ids):
        assert thumbnail_cache.get_photo_thumbnail(p)
    assert (len(plain_thumbs()), len(private_thumbs())) == (4, 2)
    # Una privada que ya no es de nadie
    (thumbnail_cache.PRIVATE_DIR / "zz").mkdir(parents=True, exist_ok=True)
    (thumbnail_cache.PRIVATE_DIR / "zz" / "zzzz.pvt").write_bytes(b"x")
    assert services.purge_cache_orphans() == 1
    assert (len(plain_thumbs()), len(private_thumbs())) == (4, 2)
    # Bloqueado no se toca el caché privado (no se pueden calcular los nombres)
    privacy.lock()
    assert services.purge_cache_orphans() == 0
    assert len(private_thumbs()) == 2


def test_pregenerar_salta_lo_oculto_si_esta_bloqueado(world):
    ids, _secret, _playa = world
    r = services.pregenerate_thumbnails(ids)
    assert (r.generated, r.already_cached) == (4, 2)
    assert not private_thumbs()
    privacy.set_pin("1234")
    r = services.pregenerate_thumbnails(ids)
    assert (r.generated, r.already_cached) == (2, 4)
    assert len(private_thumbs()) == 2


def test_limpiar_cache_incluye_el_privado(world):
    ids, _secret, _playa = world
    privacy.set_pin("1234")
    for p in db.get_photos_by_ids(ids):
        thumbnail_cache.get_photo_thumbnail(p)
    assert thumbnail_cache.cache_size_mb() >= 0
    thumbnail_cache.clear_cache()
    assert not plain_thumbs() and not private_thumbs()
