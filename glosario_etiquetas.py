r"""Que quiere decir cada etiqueta del vocabulario, en castellano.

Las 101 etiquetas son las que el modelo sabe reconocer, y estan en ingles. En
el repaso («¿esta foto es waterfall?») hace falta saber dos cosas distintas:

  1. que significa la palabra  -> la traduccion
  2. donde corta el modelo     -> el "ojo", que aclara los casos de borde

El punto 2 es el que importa: `mountain` y `mountain landscape` son etiquetas
distintas, `bar` no es `cafe`, y `macro close-up photo` es una forma de sacar
la foto, no un tema. Sin eso el repaso se contesta a ojimetro y los veredictos
quedan inconsistentes, que es justo lo contrario de para lo que sirven.

Es un diccionario y no una tabla porque no lo edita nadie desde la web: cambia
solo si cambia el vocabulario del modelo, y eso pasa en el pipeline.
"""

GLOSARIO = {
    "a baby":                   ("un bebé", "Bebé o niño muy chico como sujeto principal."),
    "aerial drone view":        ("vista aérea / dron", "Tomada desde arriba, con dron o desde un avión."),
    "airplane":                 ("avión", "El avión en sí, por fuera o en el aeropuerto."),
    "at a picnic":              ("picnic", "Comida al aire libre, sobre manta o pasto."),
    "baby shower":              ("baby shower", "Fiesta previa al nacimiento, con su decoración típica."),
    "bar":                      ("bar", "Barra con tragos y luz baja. Si se sirve comida en mesas es «restaurant»."),
    "beach":                    ("playa", "Arena y mar. Si es solo agua abierta, mirá «lake» o «boat»."),
    "bedroom":                  ("dormitorio", "Interior con cama."),
    "bicycle":                  ("bicicleta", "La bici como objeto. Si alguien la está usando, «cycling»."),
    "bird":                     ("pájaro / ave", "El ave como SUJETO de la foto: un pájaro en una rama, un pato en el lago, un primer plano de un gallo. Gallinas dentro de una escena de granja van en «farm animals» y acá se contestan que no."),
    "birthday cake":            ("torta de cumpleaños", "La torta en primer plano, con o sin velas."),
    "birthday party":           ("fiesta de cumpleaños", "La escena entera: gente, decoración, festejo."),
    "black and white photo":    ("foto en blanco y negro", "Sin color. Es una propiedad de la imagen, no un tema."),
    "blurry unusable photo":    ("foto movida o inservible", "Fuera de foco o barrida al punto de no servir."),
    "boat":                     ("bote / barco", "Embarcación de cualquier tamaño."),
    "bridge":                   ("puente", "El puente como sujeto."),
    "cafe":                     ("café / cafetería", "Café y algo dulce. Con tragos y noche es «bar»."),
    "camping":                  ("camping", "Carpas, fogón, acampar."),
    "car":                      ("auto", "El auto como sujeto, no de fondo en la calle."),
    "cat":                      ("gato", "Gato doméstico."),
    "children playing":         ("chicos jugando", "Niños en juego, no posando."),
    "christmas celebration":    ("navidad", "Árbol, luces, cena de navidad."),
    "church interior":          ("interior de iglesia", "Adentro. Por fuera suele caer en «historic building»."),
    "city skyline":             ("skyline de ciudad", "Perfil de la ciudad a lo lejos. A nivel de vereda es «city street»."),
    "city street":             ("calle de ciudad", "A nivel de calle: veredas, autos, locales."),
    "classroom":                ("sala de clases", "Aula con pupitres o pizarrón."),
    "completely dark photo":    ("foto negra", "Sin información visible. Descarte, no un tema."),
    "concert":                  ("concierto", "Show en vivo con público. Si es al aire libre y grande, «music festival»."),
    "cooking":                  ("cocinando", "Alguien preparando comida. El plato terminado es «plate of food»."),
    "couple together":          ("pareja", "Dos personas juntas como pareja."),
    "crowd of people":          ("multitud", "Mucha gente sin un sujeto claro."),
    "cycling":                  ("andando en bici", "Alguien pedaleando."),
    "dancing":                  ("bailando", "Gente bailando."),
    "desert":                   ("desierto", "Paisaje árido, arena o piedra."),
    "dessert or cake":          ("postre o torta", "Dulce que no es de cumpleaños."),
    "document or receipt":      ("documento o boleta", "Papel fotografiado: boleta, ficha, pantalla de texto."),
    "dog":                      ("perro", "Perro doméstico."),
    "drinks and cocktails":     ("tragos", "Vasos y cócteles en primer plano."),
    "eating a meal":            ("comiendo", "Gente comiendo. El plato solo es «plate of food»."),
    "family photo":             ("foto familiar", "Grupo familiar posando."),
    "farm":                     ("campo / granja", "El lugar: galpones, cultivos, corrales."),
    "farm animals":             ("animales de granja", "Ganado y aves de corral en contexto de campo: vacas, ovejas, gallinas, chanchos. Si el bicho es el sujeto y no importa la granja, mirá «bird» o «horse»."),
    "fireworks":                ("fuegos artificiales", "Explosiones de luz en el cielo."),
    "fish underwater":          ("peces bajo el agua", "Tomada dentro del agua."),
    "flowers":                  ("flores", "Flores como sujeto principal, sueltas o en planta."),
    "foggy landscape":          ("paisaje con niebla", "Neblina que define la escena. No confundir con foto movida."),
    "forest":                   ("bosque", "Muchos árboles juntos."),
    "funeral":                  ("funeral", "Velorio o entierro."),
    "garden":                   ("jardín", "Jardín cuidado, con plantas y a veces flores."),
    "graduation ceremony":      ("graduación", "Toga, birrete, entrega de diplomas."),
    "group photo of people":    ("foto grupal", "Varias personas posando juntas."),
    "gym":                      ("gimnasio", "Máquinas y pesas."),
    "hiking":                   ("trekking", "Caminata en la naturaleza, con mochila o sendero."),
    "historic building":        ("edificio histórico", "Arquitectura antigua o patrimonial, por fuera."),
    "horse":                    ("caballo", "Caballo como sujeto."),
    "hospital":                 ("hospital", "Interior clínico: camillas, box, sala."),
    "hotel room":               ("pieza de hotel", "Habitación de hotel."),
    "insect":                   ("insecto", "Bicho como sujeto. Suele venir con «macro close-up photo»."),
    "kitchen":                  ("cocina", "El espacio de la cocina."),
    "lake":                     ("lago", "Agua quieta rodeada de tierra. Si corre, «river»."),
    "living room":              ("living", "Sala de estar con sillones."),
    "macro close-up photo":     ("macro / primer plano", "Muy cerca del sujeto. Es cómo está tomada, no de qué."),
    "monument":                 ("monumento", "Estatua o monumento conmemorativo."),
    "motorcycle":               ("moto", "Moto como sujeto."),
    "mountain":                 ("montaña", "El cerro como sujeto, de cerca o dominando el cuadro."),
    "mountain landscape":       ("paisaje de montaña", "Vista amplia de cordillera. Más lejos y más abierto que «mountain»."),
    "museum":                   ("museo", "Adentro de un museo o galería."),
    "music festival":           ("festival de música", "Show grande al aire libre. En sala cerrada es «concert»."),
    "new year party":           ("año nuevo", "Fiesta de fin de año."),
    "night scene with lights":  ("escena nocturna con luces", "De noche, con luces artificiales marcando la escena."),
    "office":                   ("oficina", "Espacio de trabajo con escritorios."),
    "parade":                   ("desfile", "Marcha o desfile por la calle."),
    "park":                     ("parque / plaza", "Verde urbano con caminos o juegos."),
    "plate of food":            ("plato de comida", "La comida servida, en primer plano."),
    "playing music":            ("tocando música", "Alguien con un instrumento."),
    "playing soccer":           ("jugando fútbol", "Partido o entrenamiento de fútbol."),
    "portrait of a person":     ("retrato", "Una sola persona, posando o de sujeto claro."),
    "rainy day":                ("día de lluvia", "Lluvia, gotas, mojado."),
    "restaurant":               ("restaurante", "Comiendo en un local. El plato solo es «plate of food»."),
    "restaurant interior":      ("interior de restaurante", "El salón, con o sin gente."),
    "river":                    ("río", "Agua corriendo. Con caída vertical es «waterfall»."),
    "screenshot of a computer screen": ("captura de pantalla", "Foto o captura de una pantalla."),
    "selfie":                   ("selfie", "Sacada con el brazo extendido o al espejo."),
    "shopping":                 ("de compras", "Comprando, con bolsas o en vitrinas."),
    "skiing":                   ("esquiando", "Esquí o snowboard."),
    "small town":               ("pueblo", "Localidad chica, baja. La ciudad grande es «city street»."),
    "snowy landscape":          ("paisaje nevado", "Nieve cubriendo el terreno."),
    "sports game":              ("partido deportivo", "Deporte con público o cancha. El fútbol tiene su «playing soccer»."),
    "starry night sky":         ("cielo estrellado", "Estrellas o vía láctea."),
    "store interior":           ("interior de tienda", "Adentro de un local comercial."),
    "sunrise":                  ("amanecer", "Sol saliendo. Distinguirlo de «sunset» sin contexto es difícil."),
    "sunset":                   ("atardecer", "Sol poniéndose, cielo naranja."),
    "surfing":                  ("surf", "Surfeando sobre una ola."),
    "swimming":                 ("nadando", "Gente en el agua, piscina o mar."),
    "vineyard":                 ("viñedo", "Hileras de parras."),
    "waterfall":                ("cascada / salto de agua", "Agua cayendo por un desnivel. Si el agua corre plana, es «river»."),
    "wedding ceremony":         ("ceremonia de matrimonio", "El acto: altar, votos, entrada."),
    "wedding reception":        ("fiesta de matrimonio", "Lo que viene después: baile, brindis, mesas."),
    "wild animals":             ("animales salvajes", "Fauna silvestre en su ambiente. En cautiverio es «zoo»."),
    "working at computers":     ("trabajando en el computador", "Gente frente a pantallas."),
    "zoo":                      ("zoológico", "Animales en recinto. Sueltos en la naturaleza es «wild animals»."),
}


def explicar(tag):
    """(traduccion, ojo) para una etiqueta. Si no esta, devuelve lo que hay."""
    return GLOSARIO.get(tag, (tag, ""))


def demo():
    """Chequeo: el glosario tiene que cubrir el vocabulario real."""
    import sys
    sys.path.insert(0, r"C:\Renzo\Proyectos\IMGorgni")
    import web_curador as w
    with w.app.app_context():
        vocab = set(w.vocabulario())
    faltan = sorted(vocab - set(GLOSARIO))
    sobran = sorted(set(GLOSARIO) - vocab)
    print("vocabulario: %d | glosario: %d" % (len(vocab), len(GLOSARIO)))
    assert not faltan, "sin traducir: %s" % faltan
    print("ok  todas las etiquetas del vocabulario tienen entrada")
    if sobran:
        print("   (sobran %d que ya no estan en el vocabulario: %s)" % (len(sobran), sobran))
    vacias = [k for k, (t, o) in GLOSARIO.items() if not t.strip()]
    assert not vacias, "traduccion vacia: %s" % vacias
    print("ok  ninguna traduccion vacia")


if __name__ == "__main__":
    demo()
