r"""The ArcGIS Pro glue: two geoprocessing tools over the diagnostic engine
and the HTML renderer.

This file is a build input, not the shipped artefact. build_toolbox.py inlines
it below the engine and the renderer to produce the single file
ArcGISPro_PerformanceAnalyser/ArcGISPro_PerformanceAnalyser.pyt, and that one
file is what you add to ArcGIS Pro. It stays a separate source because it is
the only part that imports arcpy, so the engine and the renderer can be tested
on a machine with no ArcGIS at all.

The two tools appear under "ArcGIS Pro Performance Analyser".

Run "Diagnostiquer la lenteur d'ArcGIS Pro" for the workstation. It writes
arcgis_pro_performance_analyser_<stamp>.html, .json and .txt into the output
folder and opens the HTML in the default browser.

Run "Analyser un projet .aprx externe" for an explicitly selected, non-active
project. That reader snapshots the file, performs bounded ZIP/JSON/source
checks and can write a verified recovery COPY beside the source. The original
is never rewritten, and the recovery choice is diagnostic-only by default.

Read this before you trust the numbers
--------------------------------------
Running the diagnostic from inside ArcGIS Pro means ArcGIS Pro is running.
That is not a detail:

  * DL-12 reports Pro as running, which is a data-loss finding in its own
    right and lands in the act-first list.
  * PF-22, the read of the .aprx layer refresh timers, is refused. Reading a
    project file while Pro may be saving it can make the save fail, so the
    tool refuses rather than cause the very failure it was run to explain.
  * AP-01, the cache fix, is refused for the same reason.

So a run from inside Pro is a workstation report minus the project-file
checks. For the complete report, close Pro and run arcgis_pro_performance_analyser.py from a command
prompt in the editor's own session. The toolbox exists for the run you can do
without leaving the application, and it says so on the page.

Nothing here signs in to anything and no host is contacted unless you name one.
The in-Pro workstation tool never changes machine state: AP-01 belongs to the
command-line run with Pro closed, and the former AP-02 GPU change is retired.
Repairs, project recovery copies and add-in quarantine are also outside this
tool, on purpose: they need Pro closed. Double-clicking
Diagnostiquer_ArcGIS_Pro.bat now opens a numbered menu for them, so an operator
who does not remember a flag is not left believing the features are missing.
Only an explicit selected-project recovery mode can write here, and it writes
a new copy only.
"""

import os
import time

import arcpy

# BEGIN MERGE-IMPORTS
# build_toolbox.py replaces this block when it inlines the two modules into the
# single-file .pyt. There, the engine and the renderer are defined above in this
# same module, so the same qualified names are served from its globals.
import arcgis_pro_performance_analyser as engine
import html_report as report
# END MERGE-IMPORTS


def _pro_release():
    """The ArcGIS Pro release this toolbox is running under, or None."""
    try:
        return "%s (%s)" % (arcpy.GetInstallInfo()["Version"],
                            arcpy.GetInstallInfo()["BuildNumber"])
    except Exception:
        return None


def _unique(path):
    """A path that does not already exist.

    the analyser's own report writer stamps to the second, so two runs inside one
    second land on the same name and the second silently overwrites the first.
    """
    root, ext = os.path.splitext(path)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    candidate = "%s_%s%s" % (root, stamp, ext)
    counter = 1
    while os.path.exists(candidate):
        candidate = "%s_%s_%d%s" % (root, stamp, counter, ext)
        counter += 1
    return candidate


def _tail(text, lines=40):
    """The last N non-empty lines of a captured stream."""
    kept = [l for l in (text or "").splitlines() if l.strip()]
    return kept[-lines:]


def _current_project_path():
    """The active project's path, or None when Pro has none/cannot answer."""
    try:
        project = arcpy.mp.ArcGISProject("CURRENT")
        return project.filePath or None
    except Exception:
        return None


def _same_path(left, right):
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == \
        os.path.normcase(os.path.abspath(right))


def _safe_stem(path):
    """A readable report filename without path punctuation."""
    stem = os.path.splitext(os.path.basename(path or "projet"))[0]
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_"
                      for c in stem)
    return cleaned.strip("_")[:80] or "projet"


def _emit_findings(document, messages):
    """Put the explanation and ordered actions in the Pro messages as well."""
    for check in document.get("checks") or []:
        if check.get("status") not in ("Fail", "Warn"):
            continue
        messages.addWarningMessage(
            "[%s] %s — %s" % (check.get("id"),
                              "ANOMALIE" if check.get("status") == "Fail"
                              else "AVERTISSEMENT", engine.check_name(check)))
        evidence = [line for line in (check.get("observed") or "").splitlines()
                    if line.strip()]
        for line in evidence[:3]:
            messages.addMessage("  Mesure : %s" % line)
        if len(evidence) > 3:
            messages.addMessage("  … %d autre(s) ligne(s) de mesure dans le "
                                "rapport." % (len(evidence) - 3))
        if check.get("explanation"):
            messages.addMessage("  Pourquoi : %s" % check["explanation"])
        if check.get("impact_fr") or check.get("impact"):
            messages.addMessage("  Impact ArcGIS Pro : %s"
                                % (check.get("impact_fr") or check.get("impact")))
        for number, step in enumerate(check.get("steps") or [], 1):
            messages.addMessage("  Étape %d : %s" % (number, step))
        if check.get("verification"):
            messages.addMessage("  Vérification : %s" % check["verification"])


RECOVERY_NONE = "Diagnostic uniquement — ne créer aucune copie"
RECOVERY_REPACK = "Reconstruire le conteneur — contenu interne inchangé"
RECOVERY_VIEWS = "Copie de récupération sans vues au démarrage"
RECOVERY_VIEWS_HISTORY = "Copie de récupération sans vues ni historique"
RECOVERY_MODES = {
    RECOVERY_NONE: "none",
    RECOVERY_REPACK: "repack",
    RECOVERY_VIEWS: "views",
    RECOVERY_VIEWS_HISTORY: "views_history",
}


class Toolbox(object):
    def __init__(self):
        self.label = "ArcGIS Pro Performance Analyser"
        self.alias = "properf"
        self.description = (
            "Diagnostique pourquoi ArcGIS Pro est lent sur ce poste, et ne "
            "rapporte jamais une trouvaille que la machine n'a pas "
            "produite. Rend un rapport HTML, contrôle par contrôle : ce qui "
            "a été mesuré, ce que cela coûte à ArcGIS Pro, et quoi faire. "
            "Lecture seule tant que les correctifs ne sont pas cochés.")
        self.tools = [Diagnose, AnalyzeProject]
        self.canRunInBackground = False


class Diagnose(object):
    """The workstation diagnostic, with the result as HTML."""

    def __init__(self):
        self.label = "Diagnostiquer la lenteur d'ArcGIS Pro"
        self.description = (
            "DIAGNOSTIC DU POSTE. Lit ce poste — registre, stockage, "
            "redirection OneDrive, réseau, processeur, carte graphique, "
            "cache, journal d'événements — et rend un rapport HTML qui dit, "
            "contrôle par contrôle, ce qui a été mesuré, ce que cela coûte à "
            "ArcGIS Pro et comment le corriger. Les contrôles portent notamment "
            "sur les modifications qui peuvent se perdre, et "
            "un qui relève dans le journal d'événements les plantages, les "
            "suspensions et les fermetures sans arrêt propre. Lecture seule, "
            "sauf si vous cochez explicitement les correctifs. Écrit un HTML, "
            "un JSON et un texte. Les paramètres par défaut suffisent pour un "
            "premier passage : ne renseignez rien.")
        self.canRunInBackground = False
        self.category = "Diagnostic"

    def getParameterInfo(self):
        def hinted(param, tooltip, long_help):
            """Attache les deux champs d'aide qu'ArcGIS Pro affiche.

            `helpMessage` est l'infobulle derrière le petit point
            d'interrogation ; `parameterHelp` est le paragraphe du volet
            d'aide de l'outil. Les deux sont remplis ici plutôt que laissés au
            libellé, parce que le libellé doit rester assez court pour tenir
            dans le volet — et un libellé court ne peut pas porter d'exemple.
            """
            param.helpMessage = tooltip
            param.parameterHelp = long_help
            return param

        out = arcpy.Parameter(
            displayName="Dossier de sortie",
            name="output_folder",
            datatype="DEFolder",
            parameterType="Optional",
            direction="Input")
        out.value = os.path.join(
            arcpy.env.scratchFolder or os.environ.get("TEMP") or ".",
            "ArcGISPro_PerformanceAnalyser")
        hinted(out,
               "Où écrire les rapports. Laissez vide pour garder le défaut.",
               "<p><b>À quoi ça sert</b> : c'est le dossier où l'outil écrit "
               "ses trois fichiers — le rapport <b>HTML</b> (celui qu'on lit), "
               "le <b>JSON</b> (pour comparer deux passages) et le <b>TXT</b> "
               "(pour coller dans un ticket).</p>"
               "<p><b>Défaut</b> : <code>&lt;scratch&gt;\\ArcGISPro_Performance"
               "Analyser</code>. Le dossier est créé s'il n'existe pas.</p>"
               "<p><b>Exemple</b> : <code>C:\\Temp\\diag_analyser</code>. "
               "Choisissez un dossier <b>local</b>, jamais un partage réseau ni "
               "un dossier OneDrive : l'écriture doit rester rapide et ne "
               "dépendre d'aucun serveur. Un rapport écrit sur un partage "
               "synchronisé peut être lu à moitié.</p>"
               "<p><b>Rien d'existant n'est écrasé</b> : deux passages dans la "
               "même seconde produisent deux jeux de fichiers distincts.</p>")

        sde = arcpy.Parameter(
            displayName="Hôte de la géodatabase d'entreprise à sonder",
            name="sde_server",
            datatype="GPString",
            parameterType="Optional",
            direction="Input")
        sde.category = "Sondes réseau (sur demande)"
        hinted(sde,
               "Nom du serveur SQL/Oracle de vos données. Vide = aucun hôte "
               "contacté.",
               "<p><b>À quoi ça sert</b> : une latence élevée vers ce serveur "
               "explique à elle seule un Pro lent à l'ouverture des cartes. Le "
               "contrôle DL-10 mesure le temps de réponse.</p>"
               "<p><b>Laissez vide si vous ne savez pas quoi mettre.</b> Vide, "
               "aucun paquet ne quitte la machine et DL-10 répond "
               "<code>Info</code> au lieu d'inventer une mesure.</p>"
               "<p>Renseigné, l'outil ouvre <b>une seule</b> connexion TCP, "
               "mesure le temps de réponse, puis referme. Il ne s'authentifie "
               "jamais et n'envoie aucun identifiant — il n'existe d'ailleurs "
               "aucun paramètre de mot de passe.</p>"
               "<p><b>Exemple</b> : <code>gisdb01</code> ou "
               "<code>gisdb01.monorg.ch</code>. C'est le serveur nommé dans "
               "vos fichiers de connexion <code>.sde</code> ; le contrôle "
               "DL-11 les liste pour vous.</p>")

        port = arcpy.Parameter(
            displayName="Port de la géodatabase",
            name="sde_port",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input")
        port.value = 1433
        port.category = "Sondes réseau (sur demande)"
        hinted(port,
               "Port du serveur. 1433 = SQL Server, le plus courant.",
               "<p><b>À quoi ça sert</b> : c'est le port sur lequel la sonde "
               "DL-10 se connecte. Un port fermé se lit comme un serveur "
               "injoignable, ce qui est une fausse piste si le moteur n'est "
               "simplement pas le bon.</p>"
               "<p><b>Défaut</b> : <code>1433</code> (SQL Server).</p>"
               "<p><b>Valeurs courantes</b> : <code>1433</code> SQL Server, "
               "<code>1521</code> Oracle, <code>5432</code> PostgreSQL.</p>"
               "<p><b>Exemple</b> : si vos <code>.sde</code> nomment un serveur "
               "PostgreSQL, mettez <code>5432</code>. Dans le doute, laissez "
               "1433 : l'outil ne devine jamais, il dit ce qu'il a trouvé.</p>"
               "<p>Les valeurs hors de 1 à 65535 sont refusées avant "
               "l'exécution.</p>")

        portal = arcpy.Parameter(
            displayName="Hôte du portail à sonder (port 443)",
            name="portal",
            datatype="GPString",
            parameterType="Optional",
            direction="Input")
        portal.category = "Sondes réseau (sur demande)"
        hinted(portal,
               "Adresse de votre portail ou d'ArcGIS Online. Vide = aucun "
               "hôte contacté.",
               "<p><b>À quoi ça sert</b> : Pro interroge le portail au "
               "démarrage et pour chaque couche publiée. Un portail lent se "
               "lit comme un Pro lent à l'ouverture, sans qu'aucune couche "
               "soit en cause. Le contrôle PF-19 mesure cette réponse.</p>"
               "<p><b>Laissez vide si vous ne savez pas quoi mettre.</b></p>"
               "<p><b>Exemple</b> : <code>gis.monorg.ch</code> pour un Portal "
               "for ArcGIS interne, ou <code>www.arcgis.com</code> pour "
               "ArcGIS Online.</p>"
               "<p>Une seule connexion TCP sur le port 443, mesurée puis "
               "refermée. Aucun identifiant n'est envoyé, aucune page n'est "
               "téléchargée.</p>")

        timeout = arcpy.Parameter(
            displayName="Délai des sondes (millisecondes)",
            name="timeout",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input")
        timeout.value = 4000
        timeout.category = "Sondes réseau (sur demande)"
        hinted(timeout,
               "Plafond d'attente des sondes réseau. Une borne, pas une "
               "attente.",
               "<p><b>À quoi ça sert</b> : c'est le temps maximal que DL-10 et "
               "PF-19 attendent avant de déclarer l'hôte injoignable.</p>"
               "<p><b>Défaut</b> : <code>4000</code> ms.</p>"
               "<p><b>C'est une borne, pas une attente</b> : une sonde qui "
               "répond en 40 ms rend la main aussitôt. Rien n'est bloqué "
               "pendant quatre secondes quand le serveur est rapide.</p>"
               "<p><b>Exemple</b> : montez à <code>10000</code> sur une liaison "
               "VPN lente, pour ne pas lire « injoignable » sur un serveur qui "
               "répond en six secondes. Descendez à <code>1500</code> pour un "
               "passage rapide en atelier.</p>"
               "<p>Ce paramètre ne concerne <b>que</b> les sondes que vous avez "
               "demandées : videz les deux hôtes et il ne sert plus à "
               "rien.</p>")

        verbose = arcpy.Parameter(
            displayName="Détaillé",
            name="verbose",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        verbose.value = False
        hinted(verbose,
               "Affiche ce que l'outil attendait et ce qu'il a réellement "
               "lu.",
               "<p><b>À quoi ça sert</b> : à <b>prouver</b> un résultat à "
               "quelqu'un. Chaque contrôle montre alors la valeur attendue et "
               "la ligne brute lue sur la machine, en plus du verdict. C'est "
               "ce qu'il faut joindre quand le support demande « sur quoi vous "
               "basez-vous ? ».</p>"
               "<p><b>Défaut</b> : décoché.</p>"
               "<p>Décoché, le rapport reste complet et tous les verdicts sont "
               "là ; seules ces lignes de preuve sont repliées.</p>"
               "<p><b>Exemple</b> : cochez-le avant un appel au support Esri, "
               "puis joignez le <code>.txt</code> — il contiendra les valeurs "
               "brutes qui appuient chaque ligne.</p>")

        text = arcpy.Parameter(
            displayName="Écrire aussi le rapport texte",
            name="write_text",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        text.value = True
        hinted(text,
               "Ajoute un .txt, pratique à coller dans un ticket.",
               "<p><b>À quoi ça sert</b> : le <code>.txt</code> reprend le "
               "rapport HTML en texte brut. C'est le format à coller dans un "
               "appel au support ou dans un courriel, parce qu'il ne dépend "
               "d'aucun navigateur et qu'il passe dans n'importe quelle "
               "messagerie.</p>"
               "<p><b>Défaut</b> : coché.</p>"
               "<p><b>Exemple</b> : laissez-le coché. Décochez-le seulement si "
               "le dossier de sortie est soumis à un quota serré, ou si vous "
               "ne voulez qu'un seul fichier.</p>"
               "<p>Le JSON est écrit dans tous les cas : c'est lui qui permet "
               "de comparer deux passages à quelques jours d'écart.</p>")

        browser = arcpy.Parameter(
            displayName="Ouvrir le rapport HTML dans le navigateur",
            name="open_browser",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        browser.value = True
        hinted(browser,
               "Ouvre le rapport à la fin. Décochez pour un lot ou un "
               "modèle.",
               "<p><b>À quoi ça sert</b> : le rapport s'ouvre dans le "
               "navigateur par défaut dès qu'il est écrit, pour ne pas avoir à "
               "chercher le fichier.</p>"
               "<p><b>Défaut</b> : coché.</p>"
               "<p><b>Exemple</b> : décochez-le si l'outil tourne dans un "
               "modèle ModelBuilder ou dans un lot — une fenêtre qui s'ouvre à "
               "chaque itération bloque le lot. Décochez-le aussi si le "
               "navigateur est bloqué par stratégie de groupe.</p>"
               "<p><b>Le fichier est écrit dans tous les cas</b> et son chemin "
               "apparaît dans les messages de l'outil : rien n'est perdu si "
               "l'ouverture échoue.</p>")

        apply_fixes = arcpy.Parameter(
            displayName="Correctifs du poste (indisponibles dans ArcGIS Pro)",
            name="apply_fixes",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        apply_fixes.value = False
        # Keep the parameter for compatibility with existing models, but never
        # let this in-Pro entry point mutate the application that hosts it.
        apply_fixes.enabled = False
        apply_fixes.category = "Lecture seule dans ArcGIS Pro"
        hinted(apply_fixes,
               "Désactivé : le diagnostic dans ArcGIS Pro ne modifie jamais le "
               "poste. Le correctif de cache se lance hors de Pro.",
               "<p><b>À quoi ça sert</b> : ce paramètre historique est "
               "conservé pour les modèles existants, mais il est "
               "<b>désactivé</b>. Le diagnostic exécuté dans ArcGIS Pro est "
               "toujours en lecture seule.</p>"
               "<p><b>Défaut</b> : <b>décoché et désactivé</b>.</p>"
               "<p><b>AP-01</b>, seul correctif de poste encore automatisé, "
               "renomme le cache sans le supprimer et affiche sa commande "
               "d'annulation. Il doit être lancé avec le script Python, "
               "<b>ArcGIS Pro fermé</b>.</p>"
               "<p><b>AP-02 est retiré</b> : forcer le GPU hautes performances "
               "peut provoquer un crash au splash screen si son pilote est "
               "instable. Si un ancien passage a appliqué AP-02, utilisez la "
               "commande <code>undo</code> enregistrée dans son rapport JSON "
               "pour restaurer exactement la valeur précédente.</p>"
               "<p><b>Exemple</b> : laissez ce paramètre désactivé ici. Pour "
               "un cache à reconstruire, fermez Pro et utilisez "
               "<code>python arcgis_pro_performance_analyser.py --apply</code> "
               "après avoir lu le diagnostic.</p>"
               "<p><b>Menu</b> : hors de Pro, double-cliquez sur "
               "<code>Diagnostiquer_ArcGIS_Pro.bat</code> pour retrouver les "
               "actions sous forme de choix numérotés : 1 diagnostic en "
               "lecture seule, 2 correctif AP-01, 3 copie de projet, "
               "4 quarantaine d'un add-in, 5 restauration. Chaque choix "
               "exécute exactement la même commande que les options en ligne "
               "de commande.</p>")

        html_out = arcpy.Parameter(
            displayName="Rapport HTML",
            name="html_report",
            datatype="GPString",
            parameterType="Derived",
            direction="Output")
        hinted(html_out,
               "Sortie : chemin du rapport HTML. Ne se remplit pas à la main.",
               "<p><b>À quoi ça sert</b> : c'est une <b>sortie dérivée</b> — "
               "l'outil la remplit lui-même avec le chemin du rapport qu'il "
               "vient d'écrire.</p>"
               "<p><b>Ne la remplissez pas</b> : elle est en lecture seule et "
               "toute valeur saisie serait ignorée.</p>"
               "<p>Elle existe pour que l'outil puisse être <b>enchaîné</b> "
               "dans un modèle ModelBuilder.</p>"
               "<p><b>Exemple</b> : dans un modèle, reliez cette sortie à un "
               "outil « Envoyer un courrier » pour joindre automatiquement le "
               "rapport du jour.</p>")

        return [out, sde, port, portal, timeout, verbose, text, browser,
                apply_fixes, html_out]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        """Refuse what the analyser's own parser would refuse, before it runs."""
        port = parameters[2].value
        if port is not None and not 1 <= int(port) <= 65535:
            parameters[2].setErrorMessage(
                "The port must be between 1 and 65535, got %s." % port)
        timeout = parameters[4].value
        if timeout is not None and int(timeout) < 1:
            parameters[4].setErrorMessage(
                "The timeout must be at least 1 millisecond.")
        for index, flag in ((1, "geodatabase host"), (3, "portal host")):
            host = parameters[index].valueAsText
            if host:
                problem = engine.host_problem(host)
                if problem:
                    parameters[index].setErrorMessage(
                        "%r is not a host name: %s" % (host, problem))
        if parameters[8].value:
            parameters[8].setErrorMessage(
                "Workstation fixes cannot run inside ArcGIS Pro. AP-01 must "
                "run from a command prompt with Pro closed. The former AP-02 "
                "GPU fix was retired because forcing an adapter can cause a "
                "startup crash; use an older report's exact undo command if "
                "that change needs to be reversed.")
        return

    def execute(self, parameters, messages):
        folder = (parameters[0].valueAsText
                  or os.path.join(os.environ.get("TEMP") or ".", "ArcGISPro_PerformanceAnalyser"))
        opts = {
            "sde_server": parameters[1].valueAsText or None,
            "sde_port": int(parameters[2].value or 1433),
            "portal": parameters[3].valueAsText or None,
            "timeout": int(parameters[4].value or 4000),
            # Defence in depth: even a caller bypassing the disabled parameter
            # and its validation cannot mutate the running Pro workstation.
            "apply": False,
        }
        verbose = bool(parameters[5].value)

        messages.addMessage("ArcGIS Pro Performance Analyser %d - reading this workstation."
                            % engine.REPORT_VERSION)
        if opts["sde_server"] or opts["portal"]:
            messages.addMessage(
                "Network probes: %s"
                % ", ".join(filter(None, [opts["sde_server"], opts["portal"]])))
        else:
            messages.addMessage(
                "No host will be contacted. Name a geodatabase or portal host "
                "to add the two bounded TCP probes.")
        messages.addMessage(
            "Note: a project folder that already lives on a share is read, "
            "because that is what Pro does when it opens the project, and "
            "PF-03 times one listing of it.")

        started = time.time()
        env = engine.Env()
        document = engine.run(env, opts)
        code = engine.exit_code(document["checks"])
        document["exit_code"] = code
        document["symptoms"] = list(engine.SYMPTOMS)
        messages.addMessage("Read %d checks in %.1f s."
                            % (len(document["checks"]), time.time() - started))

        if not os.path.isdir(folder):
            os.makedirs(folder)
        html_path = _unique(os.path.join(folder, "arcgis_pro_performance_analyser.html"))
        json_path = os.path.splitext(html_path)[0] + ".json"

        meta = {}
        release = _pro_release()
        if release:
            meta["ArcGIS Pro"] = release
        meta["ran from"] = "the ArcGIS Pro geoprocessing pane, so Pro was open"

        report.write_html(document, html_path, verbose, code, meta)
        handle = open(json_path, "wb")
        try:
            import json as _json
            handle.write(_json.dumps(document, indent=1, sort_keys=True)
                         .encode("utf-8"))
        finally:
            handle.close()
        written = [html_path, json_path]

        if parameters[6].value:
            text_path = os.path.splitext(html_path)[0] + ".txt"
            handle = open(text_path, "wb")
            try:
                handle.write(("\n".join(engine.render(document, verbose))
                              + "\n").encode("utf-8"))
            finally:
                handle.close()
            written.append(text_path)

        # The summary, in the messages window, so the operator sees the
        # verdict without leaving ArcGIS Pro.
        for status, count in engine.counts(document["checks"]):
            if count:
                messages.addMessage("  %-8s %d" % (status.upper(), count))
        _emit_findings(document, messages)
        if document["act_first"]:
            messages.addWarningMessage(
                "ACT ON THESE FIRST - they bear on the vanished edits: %s"
                % ", ".join(document["act_first"]))
        for note in document["notes"]:
            messages.addMessage("  fix gate: %s" % note)
        for record in document["applied"]:
            messages.addWarningMessage(
                "APPLIED [%s] %s  undo: %s"
                % (record["id"], record["result"], record["undo"]))
        for path in written:
            messages.addMessage("  report: %s" % path)

        parameters[9].value = html_path

        if parameters[7].value:
            try:
                import webbrowser
                webbrowser.open("file:///" + html_path.replace("\\", "/"))
                messages.addMessage("Opened the HTML report in the default "
                                    "browser.")
            except Exception as exc:
                messages.addWarningMessage(
                    "Could not open a browser (%s). The report is at %s"
                    % (exc, html_path))

        messages.addMessage(
            "The report carries the account name, local paths, connection file "
            "names and exclusion paths. Read it before you send it to anyone.")
        messages.addMessage("Analyser exit code %d: %s"
                            % (code, report.EXIT_MEANING.get(code, "?")))
        return


class AnalyzeProject(object):
    """Read one explicitly selected, non-active .aprx and optionally recover a copy."""

    def __init__(self):
        self.label = "Analyser un projet .aprx externe"
        self.description = (
            "Sélectionnez un fichier .aprx autre que le projet actuellement "
            "ouvert. L'outil en fait une copie instantanée temporaire, vérifie "
            "le conteneur ZIP, les documents JSON, les vues restaurées, "
            "l'historique, les sources et la charge des cartes. Le rapport "
            "explique chaque problème, son impact dans ArcGIS Pro et les "
            "étapes de correction. En option, il crée une copie de récupération "
            "sans vues au démarrage et/ou sans historique. L'original n'est "
            "jamais modifié.")
        self.canRunInBackground = False
        self.category = "Diagnostic"

    def getParameterInfo(self):
        def hinted(param, tooltip, long_help):
            param.helpMessage = tooltip
            param.parameterHelp = long_help
            return param

        project = arcpy.Parameter(
            displayName="Projet .aprx à analyser (pas le projet ouvert)",
            name="project",
            datatype="DEFile",
            parameterType="Required",
            direction="Input")
        project.filter.list = ["aprx"]
        hinted(project,
               "Sélectionnez un .aprx différent de celui qui est ouvert dans "
               "cette session ArcGIS Pro.",
               "<p><b>À quoi ça sert</b> : choisir précisément le projet à "
               "diagnostiquer, au lieu d'analyser les projets récents ou le "
               "projet actif.</p><p><b>Obligatoire</b> : un fichier "
               "<code>.aprx</code>. Le projet actuellement ouvert est refusé, "
               "même si vous demandez seulement un diagnostic.</p>"
               "<p>L'outil travaille sur une <b>copie instantanée temporaire"
               "</b>. Si le fichier change pendant cette copie, l'analyse est "
               "abandonnée pour ne pas mélanger deux enregistrements.</p>"
               "<p><b>Exemple</b> : <code>C:\\GIS\\Anciens\\Parcelles.aprx</code>."
               " Vous pouvez laisser un autre projet ouvert dans Pro.</p>")

        out = arcpy.Parameter(
            displayName="Dossier des rapports",
            name="output_folder",
            datatype="DEFolder",
            parameterType="Optional",
            direction="Input")
        out.value = os.path.join(
            arcpy.env.scratchFolder or os.environ.get("TEMP") or ".",
            "ArcGISPro_PerformanceAnalyser")
        hinted(out,
               "Dossier local où écrire le HTML, le JSON et le TXT.",
               "<p><b>À quoi ça sert</b> : recevoir les trois rapports. Ce "
               "dossier ne reçoit <b>pas</b> la copie de récupération du "
               "projet.</p><p><b>Défaut</b> : <code>&lt;scratch&gt;\\ArcGISPro_"
               "PerformanceAnalyser</code>.</p><p>La copie .aprx est écrite "
               "<b>à côté de l'original</b> pour préserver les chemins relatifs "
               "des données. Son nom reçoit un suffixe explicite et un fichier "
               "existant n'est jamais écrasé.</p>")

        repair = arcpy.Parameter(
            displayName="Action de récupération sur une copie",
            name="repair_mode",
            datatype="GPString",
            parameterType="Optional",
            direction="Input")
        repair.value = RECOVERY_NONE
        repair.filter.type = "ValueList"
        repair.filter.list = [RECOVERY_NONE, RECOVERY_REPACK, RECOVERY_VIEWS,
                              RECOVERY_VIEWS_HISTORY]
        repair.category = "Récupération — jamais sur l'original"
        hinted(repair,
               "Diagnostic seul par défaut. Les autres choix créent un nouveau "
               ".aprx à côté de l'original.",
               "<p><b>À quoi ça sert</b> : tenter une récupération ciblée "
               "<b>sans jamais réécrire le projet d'origine</b>.</p>"
               "<ul><li><b>Diagnostic uniquement</b> (défaut) : aucun nouveau "
               "projet.</li><li><b>Reconstruire le conteneur</b> : réécrit le "
               "ZIP sans changer les documents ; utile seulement si tous les "
               "éléments sont lisibles.</li><li><b>Sans vues au démarrage</b> : "
               "retire <code>views</code> et <code>viewLayoutXML</code> de la "
               "copie pour ne pas rouvrir une carte ou mise en page qui fait "
               "planter Pro. Les définitions des cartes et mises en page sont "
               "conservées.</li><li><b>Sans vues ni historique</b> : retire "
               "aussi les éléments <code>GPHistory</code> de la copie.</li></ul>"
               "<p><b>Garanties</b> : intégrité revérifiée avant de nommer la "
               "copie, aucun écrasement, original intact. La récupération est "
               "refusée si les contrôles nécessaires ne sont pas Conforme. "
               "Elle ne garantit pas de réparer toutes les corruptions.</p>")

        verbose = arcpy.Parameter(
            displayName="Inclure les preuves détaillées",
            name="verbose",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        verbose.value = False
        hinted(verbose,
               "Ajoute les valeurs attendues et preuves techniques au TXT.",
               "<p><b>À quoi ça sert</b> : conserver les détails nécessaires "
               "à un ticket de support.</p><p><b>Défaut</b> : décoché. Les "
               "explications, impacts et étapes restent affichés dans tous "
               "les cas.</p><p><b>Exemple</b> : cochez avant d'envoyer le "
               "rapport à Esri ou à votre administrateur SIG.</p>")

        text = arcpy.Parameter(
            displayName="Écrire aussi le rapport texte",
            name="write_text",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        text.value = True
        hinted(text,
               "Ajoute un TXT contenant les mêmes étapes de correction.",
               "<p><b>À quoi ça sert</b> : copier facilement le diagnostic "
               "dans un ticket ou un courriel.</p><p><b>Défaut</b> : coché. "
               "Le HTML et le JSON sont écrits dans tous les cas.</p>")

        browser = arcpy.Parameter(
            displayName="Ouvrir le rapport HTML",
            name="open_browser",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        browser.value = True
        hinted(browser,
               "Ouvre le rapport à la fin de l'analyse.",
               "<p><b>À quoi ça sert</b> : lire immédiatement les explications "
               "et les étapes numérotées.</p><p><b>Défaut</b> : coché. Le "
               "chemin du fichier reste affiché même si le navigateur ne "
               "s'ouvre pas.</p>")

        html_out = arcpy.Parameter(
            displayName="Rapport HTML du projet",
            name="html_report",
            datatype="GPString",
            parameterType="Derived",
            direction="Output")
        hinted(html_out,
               "Sortie dérivée : chemin du rapport HTML.",
               "<p><b>À quoi ça sert</b> : chaîner le rapport dans un modèle. "
               "Ne renseignez pas cette sortie vous-même.</p>")

        recovered = arcpy.Parameter(
            displayName="Projet de récupération créé",
            name="recovered_project",
            datatype="DEFile",
            parameterType="Derived",
            direction="Output")
        hinted(recovered,
               "Sortie dérivée : nouveau .aprx, vide en diagnostic seul.",
               "<p><b>À quoi ça sert</b> : retrouver la copie créée et la "
               "chaîner à un autre outil.</p><p>Elle reste vide en diagnostic "
               "seul, si aucune modification n'est nécessaire, ou si les "
               "contrôles de sécurité refusent la récupération.</p>")
        return [project, out, repair, verbose, text, browser, html_out, recovered]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        path = parameters[0].valueAsText.strip()
        if path and not path.lower().endswith(".aprx"):
            parameters[0].setErrorMessage(
                "Sélectionnez un fichier portant l'extension .aprx.")
        elif path and not os.path.isfile(path):
            parameters[0].setErrorMessage(
                "Le fichier sélectionné est introuvable ou inaccessible : %s" % path)
        current = _current_project_path()
        if path and _same_path(path, current):
            parameters[0].setErrorMessage(
                "Ce .aprx est le projet actuellement ouvert. Enregistrez-le, "
                "ouvrez un autre projet, puis sélectionnez à nouveau ce fichier "
                "ou utilisez une copie. L'outil refuse le projet actif.")
        mode = parameters[2].valueAsText or RECOVERY_NONE
        if mode not in RECOVERY_MODES:
            parameters[2].setErrorMessage(
                "Choisissez une des quatre actions proposées dans la liste.")
        elif mode != RECOVERY_NONE:
            parameters[2].setWarningMessage(
                "L'original restera intact. La récupération crée un nouveau "
                "fichier à côté de lui et seulement si les contrôles d'intégrité "
                "l'autorisent.")
        if mode == RECOVERY_VIEWS_HISTORY:
            parameters[0].setWarningMessage(
                "L'historique de géotraitement sera retiré de la copie seulement. "
                "Conservez l'original pour retrouver les anciens paramètres.")
        return

    def execute(self, parameters, messages):
        path = parameters[0].valueAsText.strip()
        current = _current_project_path()
        if not path or not path.lower().endswith(".aprx"):
            raise arcpy.ExecuteError("Un fichier .aprx doit être sélectionné.")
        if _same_path(path, current):
            raise arcpy.ExecuteError(
                "Le projet actif ne peut pas être analysé par cet outil. "
                "Ouvrez un autre projet ou sélectionnez une copie.")
        mode_label = parameters[2].valueAsText or RECOVERY_NONE
        if mode_label not in RECOVERY_MODES:
            raise arcpy.ExecuteError("Action de récupération inconnue.")
        mode = RECOVERY_MODES[mode_label]
        folder = (parameters[1].valueAsText or os.path.join(
            os.environ.get("TEMP") or ".", "ArcGISPro_PerformanceAnalyser"))
        verbose = bool(parameters[3].value)

        messages.addMessage("Analyse du projet externe : %s" % path)
        messages.addMessage("Copie instantanée temporaire ; projet actif et "
                            "fichier source non modifiés par le diagnostic.")
        messages.addMessage("Action : %s" % mode_label)
        started = time.time()
        document = engine.run_project(engine.Env(), {
            "project_path": path, "repair": mode})
        code = engine.exit_code(document["checks"])
        document["exit_code"] = code
        messages.addMessage("%d contrôles en %.1f s."
                            % (len(document["checks"]), time.time() - started))

        if not os.path.isdir(folder):
            os.makedirs(folder)
        base = "diagnostic_projet_%s.html" % _safe_stem(path)
        html_path = _unique(os.path.join(folder, base))
        json_path = os.path.splitext(html_path)[0] + ".json"
        meta = {"Projet analysé": path,
                "Action demandée": mode_label,
                "Méthode": "copie instantanée locale ; original jamais réécrit"}
        release = _pro_release()
        if release:
            meta["ArcGIS Pro"] = release
        report.write_html(document, html_path, verbose, code, meta)
        handle = open(json_path, "wb")
        try:
            import json as _json
            handle.write(_json.dumps(document, indent=1, sort_keys=True)
                         .encode("utf-8"))
        finally:
            handle.close()
        written = [html_path, json_path]
        if parameters[4].value:
            text_path = os.path.splitext(html_path)[0] + ".txt"
            handle = open(text_path, "wb")
            try:
                handle.write(("\n".join(engine.render(document, verbose))
                              + "\n").encode("utf-8"))
            finally:
                handle.close()
            written.append(text_path)

        for status, count in engine.counts(document["checks"]):
            if count:
                messages.addMessage("  %-8s %d" % (status.upper(), count))
        _emit_findings(document, messages)
        for note in document["notes"]:
            if "REFUSÉE" in note or "ÉCHEC" in note:
                messages.addWarningMessage(note)
            else:
                messages.addMessage(note)
        for record in document["applied"]:
            messages.addWarningMessage(
                "COPIE CRÉÉE [%s] %s\n%s\nRetour arrière : %s"
                % (record["id"], record["what"], record["result"],
                   record["undo"]))
        for written_path in written:
            messages.addMessage("  report: %s" % written_path)
        parameters[6].value = html_path
        parameters[7].value = document.get("recovery", {}).get("path") or ""

        if parameters[5].value:
            try:
                import webbrowser
                webbrowser.open("file:///" + html_path.replace("\\", "/"))
                messages.addMessage("Rapport HTML ouvert dans le navigateur.")
            except Exception as exc:
                messages.addWarningMessage(
                    "Ouverture du navigateur impossible (%s). Rapport : %s"
                    % (exc, html_path))
        messages.addMessage("Le rapport contient des chemins et noms de couches. "
                            "Relisez-le avant de le transmettre.")
        messages.addMessage("Code de sortie %d : %s"
                            % (code, report.EXIT_MEANING.get(code, "?")))
        return
