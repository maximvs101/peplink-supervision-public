"""Les trois règles d'alerte, et rien d'autre.

Une alerte n'est ni le bandeau ni le journal. Le bandeau dit l'état de
l'instant, lissé. Le journal dit ce qui s'est passé, listé. Une alerte dit
« ceci demande une décision, et ça n'est pas passé tout seul » — elle a une
DURÉE, et elle survit un moment à sa propre disparition pour que quelqu'un qui
s'est éloigné deux minutes l'apprenne.

**Pourquoi ces trois-là, et pas d'autres.** Les règles candidates ont été
éprouvées sur 14 h 30 de relevés réels du matériel de référence, et la plupart
sont mortes à l'épreuve :

  règle candidate                        déclenchements   verdict
  un lien hors du bond > 1 min                        3   RETENUE, 0 faux positif
  liens porteurs tombés à 1 ou 0                      0   RETENUE (voir plus bas)
  la supervision s'est interrompue                    —   RETENUE, angle mort
  des modems partagent une cellule          99 % des cycles   papier peint
  aller-retour au-delà d'une seconde                 10   sans conséquence
  niveau radio faible                                 —   ne prédit rien (ρ ≤ 0,19)

Une règle qui se déclencherait 99 % du temps n'est pas une alerte, c'est du
papier peint : on cesse de la voir, et elle emporte les autres avec elle.

**Zéro déclenchement n'est pas un défaut.** La deuxième règle n'a pas parlé une
fois en 14 h 30, et c'est exactement ce qu'on attend d'elle : elle reste muette
jusqu'au jour où elle compte. Ce qui la disqualifierait serait de parler pour
rien, pas de se taire quand tout va bien. On ne peut pas pour autant la dire
« prouvée » : elle est seulement non réfutée.

**Ce qu'une alerte ne fait jamais.** Se déclencher sur une donnée ABSENTE. Un
état de tunnel inconnu n'est pas un lien hors du bond ; un cycle où la sonde n'a
pas tourné n'est pas un cycle sans lien porteur. C'est le même défaut que le
zéro pour une absence, et il coûterait ici plus cher qu'ailleurs : une alerte
qu'on découvre injustifiée est une alerte qu'on n'écoutera plus.
"""

from __future__ import annotations

from datetime import datetime

from .models import Alert, WanRadioStatus, utcnow

# --- les seuils, et ils sont publiés ---------------------------------------

# Durée pendant laquelle un lien doit rester hors du bond avant qu'on le
# signale. Un lien en sort et y rentre au gré des bascules de cellule ; en
# dessous d'une minute, l'alerte suivrait ce va-et-vient. Éprouvée sur la base :
# trois déclenchements en 14 h 30, aucun faux positif.
HORS_BOND_S = 60.0

# Durée pendant laquelle la liaison doit tenir sur un seul lien — ou aucun —
# avant qu'on le signale. Plus court que la précédente parce que le fait est
# plus grave : il ne reste plus rien à perdre. Trois cycles suffisent à écarter
# un relevé isolé.
SEUL_LIEN_S = 15.0

# Interruption de supervision au-delà de laquelle on la signale au redémarrage.
# En dessous, c'est un redémarrage de service ordinaire — le nôtre — et le dire
# ferait une alerte à chaque mise à jour.
INTERRUPTION_S = 300.0

# Durée pendant laquelle une alerte RÉSOLUE reste affichée.
#
# Sans elle, une coupure de trois minutes survenue pendant qu'on préparait le
# matériel n'aurait jamais existé pour l'exploitant. Avec elle, l'écran dit
# « c'est revenu, et voilà combien de temps ça a duré ». Au-delà, c'est au
# journal de répondre : il date tout et ne s'efface pas.
REMANENCE_S = 600.0


def _cle(alerte: Alert) -> tuple[str, str | None]:
    return (alerte.kind, alerte.wan_id)


# Les deux genres de la règle 2, qui se relaient sans rompre la durée.
_FAMILLE_BOND = frozenset({"liaison sur un seul lien", "liaison sans aucun lien"})


def evaluer(
    statuses: list[WanRadioStatus],
    suivies: list[Alert],
    *,
    maintenant: datetime | None = None,
    tunnel_attendu: bool = True,
) -> list[Alert]:
    """Applique les trois règles à un relevé, et rend l'état SUIVI des alertes.

    Ce qui sort n'est pas ce qui s'affiche : une condition vraie depuis deux
    secondes est ici, mais `publiables()` ne la retiendra pas. La distinction
    est nécessaire — la première version ne reportait que les alertes publiées,
    si bien qu'une condition en attente perdait son instant de départ à chaque
    cycle et ne franchissait jamais son seuil. Elle ne se serait jamais
    déclenchée, et rien dans son code ne le disait.

    `suivies` porte l'état précédent : c'est lui qui donne leur DURÉE aux
    conditions, et c'est la durée qui distingue un fait d'un clignotement.

    **Un équipement injoignable ne passe pas par ici.** Le cycle sort avant, et
    l'état des alertes reste donc FIGÉ sur ce qu'on savait en dernier. C'est
    délibéré : déclarer résolue une condition qu'on ne peut plus observer
    l'affirmerait sans l'avoir vue. La contrepartie est que la durée affichée
    continue de courir alors que plus rien ne la mesure — mais un équipement
    injoignable est déjà annoncé comme tel par le bandeau, et deux alarmes pour
    un fait en diluent une.
    """
    maintenant = maintenant or utcnow()
    # Même règle que pour l'équipement injoignable : quand la sonde du tunnel
    # n'a rien rendu ce cycle-ci — aucun lien ne porte d'état de tunnel — on
    # ne peut ni ouvrir ni résoudre ce qui dépend du bond. Sans cette garde,
    # un cycle sans sonde CLÔTURAIT tout : une condition en attente repartait
    # de zéro, une alerte publiée était déclarée résolue puis renaissait non
    # publiée pendant soixante secondes. Le module promettait de ne jamais se
    # déclencher sur une absence de mesure ; il se résolvait dessus.
    if tunnel_attendu and not any(
        s.tunnel is not None and s.tunnel.state is not None for s in statuses
    ):
        return list(suivies)
    connues = {_cle(a): a for a in suivies}
    sortie: list[Alert] = []
    vues: set[tuple[str, str | None]] = set()
    conditions = list(_conditions(statuses, tunnel_attendu=tunnel_attendu))
    # Les alertes par lien encore OUVERTES : c'est d'elles qu'une alerte de
    # niveau bond qui les absorbe hérite son début. Sans cela, le pair qui
    # tombe fermait trois alertes publiées et en ouvrait une quatrième à
    # zéro — quinze secondes de « résolues » et rien d'actif, en pleine panne.
    par_lien_ouvertes = [
        a for a in suivies if a.wan_id is not None and a.until is None
    ]

    for kind, wan_id, detail, seuil, absorbee in conditions:
        cle = (kind, wan_id)
        vues.add(cle)
        ancienne = connues.get(cle)
        # Une alerte résolue puis reparue est une alerte NEUVE : reprendre son
        # `since` fusionnerait deux faits séparés en un seul, plus long
        # qu'aucun des deux.
        if ancienne is not None and ancienne.until is None:
            debut = ancienne.since
        else:
            debut = maintenant
            # Les deux genres de la règle 2 sont une même FAMILLE : passer de
            # « un seul » à « aucun » n'est pas une alerte qui cesse et une
            # autre qui commence, c'est la même qui s'aggrave. Séparés, chaque
            # passage 1 ↔ 0 fermait l'une et repartait l'autre de zéro : un
            # dernier lien qui bat ne publiait jamais rien, et la chute du
            # dernier porteur s'affichait « résolue ».
            if wan_id is None:
                for jumelle_kind in _FAMILLE_BOND - {kind}:
                    jumelle = connues.get((jumelle_kind, None))
                    if jumelle is not None and jumelle.until is None:
                        debut = jumelle.since
                        vues.add((jumelle_kind, None))
                        break
            # Une alerte de bond née d'une cause commune hérite du plus ancien
            # des liens qu'elle absorbe.
            if wan_id is None and " — " in detail and par_lien_ouvertes:
                debut = min([debut] + [a.since for a in par_lien_ouvertes])
        sortie.append(
            Alert(
                kind=kind, wan_id=wan_id, since=debut, until=None, detail=detail,
                # Le seuil de durée voyage avec l'alerte : l'exploitant doit
                # pouvoir savoir sur quoi elle se fonde sans lire le code.
                sustained_s=seuil, absorbee=absorbee,
            )
        )

    for cle, ancienne in connues.items():
        if cle in vues:
            continue
        close = (
            ancienne
            if ancienne.until is not None
            else ancienne.model_copy(update={"until": maintenant})
        )
        # Une condition qui n'a jamais franchi son seuil n'a jamais été une
        # alerte : elle disparaît sans laisser de trace, plutôt que d'apparaître
        # au moment même où elle cesse — le pire des deux mondes, tardive ET
        # fausse.
        duree = (close.until - close.since).total_seconds()
        if duree < close.sustained_s:
            continue
        if (maintenant - close.until).total_seconds() <= REMANENCE_S:
            sortie.append(close)

    sortie.sort(key=lambda a: (a.until is not None, a.since))
    return sortie


def publiables(suivies: list[Alert], *, maintenant: datetime | None = None) -> list[Alert]:
    """Celles qui ont franchi leur seuil de durée, et elles seules.

    Une condition qui vient d'apparaître est suivie mais pas annoncée : c'est
    ce délai qui fait la différence entre une alerte et un clignotement. Quand
    elle est enfin publiée, `since` porte l'instant où elle a COMMENCÉ, pas
    celui où on s'est décidé à le dire.
    """
    maintenant = maintenant or utcnow()
    return [
        a
        for a in suivies
        if not a.absorbee
        and ((a.until or maintenant) - a.since).total_seconds() >= a.sustained_s
    ]


def _conditions(statuses: list[WanRadioStatus], *, tunnel_attendu: bool = True):
    """Les conditions VRAIES à cet instant, sans considération de durée."""
    # Un équipement sans le moindre objet tunnel n'a pas de bond : ni la
    # première règle ni la deuxième ne s'y appliquent, et les y appliquer
    # signalerait l'absence de SpeedFusion comme une panne.
    tunnels = [s for s in statuses if s.tunnel is not None]
    if not tunnels:
        return
    # Même chose pour un bond qui n'a JAMAIS porté de lien sur cet
    # équipement : un tunnel qui n'a jamais tenu n'est pas tombé, c'est un
    # état — l'exploitant l'a dit du MAX HD2, où SpeedFusion n'est pas censé
    # fonctionner. Une alerte est une nouvelle ; ici il n'y en a pas. Le
    # bandeau, lui, dit l'état. Dès que le bond porte une fois, la réponse
    # change et les deux règles s'appliquent.
    if not tunnel_attendu:
        return

    # --- 1. un lien hors du bond -------------------------------------------
    #
    # `state is None` est un état INCONNU, ni dedans ni dehors : le tunnel n'a
    # pas été sondé à ce cycle. Une alerte sur une absence de mesure est
    # exactement ce que cette règle ne doit pas faire.
    #
    # Un lien DÉCONNECTÉ est écarté aussi : qu'il ne porte plus rien n'apprend
    # rien de neuf, et le dire deux fois dilue les deux.
    #
    # Le dénominateur doit être OBSERVÉ. Si aucun lien n'a d'état de tunnel à
    # ce cycle, on ne sait pas combien portent — et zéro porteur constaté sur
    # zéro lien regardé n'est pas une panne, c'est une absence de regard. Une
    # seule population, dérivée une fois : trois compréhensions du même
    # prédicat, et le repli ci-dessous reposait sur leur égalité implicite.
    regardes = [s for s in tunnels if s.tunnel is not None and s.tunnel.state is not None]
    if not regardes:
        return
    debout = [s for s in regardes if s.connected]
    ecartes = [s for s in debout if s.tunnel is not None and not s.tunnel.in_bond]

    # Une cause CÔTÉ PAIR qui écarte TOUS les liens est un seul événement, pas
    # N. Sur le MAX HD2 dont le concentrateur était injoignable, l'écran
    # empilait « Lien 1 hors du bond PEER_DOWN », « Lien 4 … », « Lien 5 … »
    # au-dessus de « plus aucun lien dans le bond » : quatre alertes pour un
    # fait, allumées en permanence tant que le pair reste hors d'atteinte — le
    # papier peint que ce module existe pour éviter. Quand tous les liens
    # debout sont écartés pour la MÊME cause et que cette cause nomme le pair,
    # seule la règle de niveau bond s'affiche. Les alertes par lien restent
    # SUIVIES, absorbées : les taire n'est pas les fermer — fermées, elles
    # s'affichaient « résolues » à l'instant où le bond tombait, et repartaient
    # de zéro au retour du pair.
    causes = {str(s.tunnel.state) for s in ecartes if s.tunnel is not None}
    # La PORTÉE de la cause est classée par le modèle, là où la sentinelle
    # naît — plus un préfixe de chaîne comparé ici.
    cause_pair = (
        bool(ecartes) and len(ecartes) == len(debout) and len(causes) == 1
        and all(s.tunnel is not None and s.tunnel.cause_scope == "pair" for s in ecartes)
    )
    for status in ecartes:
        lien = status.tunnel
        assert lien is not None
        # La CAUSE vient du routeur, pas d'une déduction : `state` porte son
        # propre mot — WAN_DOWN, WAN_DISABLED, COLD…
        yield ("hors du bond", status.wan_id, lien.state, HORS_BOND_S, cause_pair)

    # --- 2. il ne reste qu'un lien porteur, ou aucun -----------------------
    porteurs = [
        s for s in debout
        if s.tunnel is not None and s.tunnel.in_bond and not s.tunnel.beyond_cutoff
    ]
    # Un porteur et aucun sont deux faits distincts, et ils portaient le même
    # genre : sur le premier équipement rencontré hors du réseau de
    # développement, un MAX HD2 dont le concentrateur était injoignable,
    # l'écran titrait « la liaison ne tient plus que sur un lien » avec le
    # détail « aucun sur 3 ». Zéro n'est pas un. Le genre dit désormais lequel.
    # « 5 seul sur 3 » se lisait comme une fraction : le lien est nommé comme
    # dans « Lien 4 hors du bond », et le total dit ce qu'il compte (30/09).
    if len(porteurs) == 1:
        yield ("liaison sur un seul lien", None,
               f"lien {porteurs[0].wan_id} seul, sur {len(regardes)} liens", SEUL_LIEN_S, False)
    elif not porteurs:
        # La cause voyage avec l'alerte de niveau bond quand elle est commune :
        # absorber les alertes par lien ne doit pas faire disparaître le mot du
        # routeur.
        detail = f"aucun sur {len(regardes)}"
        if cause_pair:
            detail += f" — {causes.copy().pop()}"
        yield ("liaison sans aucun lien", None, detail, SEUL_LIEN_S, False)


def interruption(
    dernier_releve: datetime | None, *, maintenant: datetime | None = None
) -> Alert | None:
    """La supervision s'est-elle interrompue, et pendant combien de temps ?

    C'est l'angle mort de l'outil, et il est structurel : quand le superviseur
    s'arrête, plus rien ne l'observe — ni lui-même, ni le routeur, qui continue
    de fonctionner sans témoin. Le trou n'est constatable qu'APRÈS, en comparant
    le dernier relevé écrit à l'heure du redémarrage.

    La bande de la fenêtre montre déjà ces creux, mais elle demande qu'on la
    regarde et qu'on sache les lire. Ici, l'outil le DIT.

    Rendue close d'emblée : le fait appartient au passé, et une alerte qui
    prétendrait durer affirmerait que la supervision est encore arrêtée alors
    qu'elle vient de reprendre.

    Sans `detail` : la DURÉE est déjà portée par `since` et `until`, et la
    redire en toutes lettres produisait deux chiffres du même fait à un
    centimètre l'un de l'autre — « aucun relevé pendant 6 min » à côté de
    « de 20:19 à 20:25 · 5 min », parce que l'un arrondit et l'autre tronque.
    Le journal du serveur, lui, garde sa phrase : il n'affiche pas les bornes.
    """
    if dernier_releve is None:
        return None
    maintenant = maintenant or utcnow()
    trou = (maintenant - dernier_releve).total_seconds()
    if trou < INTERRUPTION_S:
        return None
    return Alert(
        kind="supervision interrompue",
        wan_id=None,
        since=dernier_releve,
        until=maintenant,
        detail=None,
        sustained_s=INTERRUPTION_S,
    )
