# ArcGIS Pro Performance Analyser — toolbox et rapport HTML

Une **Python Toolbox** (`.pyt`) qui expose le diagnostic de poste dans ArcGIS Pro
et rend le résultat en **un fichier HTML autonome** (aucune ressource externe,
filtres locaux, JSON embarqué pour le diff).

## Un seul fichier

Le moteur de diagnostic et le moteur de rendu sont **inlinés dans le `.pyt`**.
Il n'y a aucun module voisin à garder à côté, donc rien qui puisse manquer sur
le poste où le fichier atterrit.

| Fichier | Rôle |
|---|---|
| `Diagnostiquer_ArcGIS_Pro.bat` (à la racine) | Menu numéroté au double-clic (lecture seule par défaut, rapports TXT, JSON et HTML), plus les options explicites AP-01, réparation de copie `.aprx` et quarantaine/restauration d'add-in pour les scripts ; nécessite le moteur et le rendu à côté de lui |
| `ArcGISPro_PerformanceAnalyser.pyt` | **L'artefact livré.** Deux outils, moteur et rendu inclus |
| `arcgis_pro_performance_analyser.py` | Le moteur de diagnostic et sa ligne de commande. Sans `arcpy` |
| `html_report.py` | Le rendu HTML. Fonction pure sur le document de rapport, sans `arcpy` |
| `toolbox_glue.py` | L'interface ArcGIS Pro. La seule partie qui importe `arcpy` |
| `build_toolbox.py` | Assemble les trois fichiers ci-dessus en un seul `.pyt` |

Le `.pyt` est **généré** : on modifie les sources, puis on lance
`python build_toolbox.py`. On ne modifie jamais le `.pyt` à la main —
`tests/test_toolbox.py` relance la génération et compare octet par octet, donc un
artefact qui a dérivé de ses sources ne peut pas rester dans le dépôt sans être
vu.

Les sources restent séparées parce qu'elles sont testables séparément : le
moteur se teste sans ArcGIS installé, le rendu est une fonction pure, et seule
l'interface a besoin d'`arcpy`. La fusion est une concaténation, et la
génération s'arrête si deux sources définissent un jour le même nom au niveau
module, parce que l'une masquerait l'autre silencieusement.

## Installation

1. Copier `ArcGISPro_PerformanceAnalyser.pyt` dans un emplacement stable, par
   exemple `C:\GIS\Tools\`.
2. Dans ArcGIS Pro : volet **Catalogue → Toolboxes → Add Toolbox**, pointer sur
   ce fichier.
3. Les deux outils apparaissent sous **« ArcGIS Pro Performance Analyser »** :
   **Diagnostiquer la lenteur d'ArcGIS Pro** et **Analyser un projet .aprx externe**.

C'est tout : il n'y a pas d'étape 4. Prérequis : ArcGIS Pro 3.x (Python 3.9+),
bibliothèque standard uniquement.

## Partager l'outil dans l'organisation

Trois fichiers suffisent : `Diagnostiquer_ArcGIS_Pro.bat`,
`arcgis_pro_performance_analyser.py` et `html_report.py`. Copiez-les **ensemble**
dans un dossier local de chaque poste — l'exécution depuis un partage réseau
n'est pas testée. Aucun n'a besoin d'être installé, d'être lancé en
administrateur, ni d'ouvrir ArcGIS Pro.

- **Rien dans le code ne désigne un poste.** Ni hôte, ni chemin de profil, ni
  nom d'add-in, ni adresse électronique : le lanceur déduit son dossier de
  rapports du compte qui l'exécute, et la suite de tests échoue si un fichier
  livré se met à nommer un vrai profil Windows. C'est ce qui rend une copie
  distribuable telle quelle.
- **Chaque utilisateur lance l'outil dans sa propre session Windows.** Les
  réglages, caches et entrées du gestionnaire d'add-ins appartiennent au compte
  qui exécute : le faire « pour » un collègue depuis votre session lirait le
  mauvais profil. Les rapports atterrissent alors dans le
  `%LOCALAPPDATA%\ArcGISPro_PerformanceAnalyser\Rapports` de cet utilisateur.
- **La machine n'est pas modifiée sans un choix explicite.** Un passage normal
  écrit des rapports et rien d'autre. Le correctif de cache, la copie de
  récupération et la quarantaine d'add-in sont des choix, chacun affiche son
  annulation, et la quarantaine copie au lieu de supprimer.
- **Un rapport est un document interne.** Il contient le nom du compte, des
  chemins Windows, les add-ins installés, la chronologie des événements et le
  dossier du projet. Partagez-le avec le support de façon délibérée, évitez les
  hébergements publics, et transmettez le JSON par un canal déjà approuvé : il
  porte la même information, lisible par machine, et c'est lui que l'on compare
  entre deux postes.
- **L'outil ne contacte aucun serveur qui lui soit propre.** Pas de télémétrie,
  pas de recherche de mise à jour ; la politique de sécurité du rapport HTML
  interdit toute ressource externe. Seuls `--sde-server` et `--portal` sortent
  sur le réseau, et seulement s'ils sont nommés.

## Diagnostic par `.bat`, sans ouvrir ArcGIS Pro

À la racine du dépôt, conservez ensemble ces trois fichiers :

- `Diagnostiquer_ArcGIS_Pro.bat` ;
- `arcgis_pro_performance_analyser.py` ;
- `html_report.py`.

**Double-cliquez sur `Diagnostiquer_ArcGIS_Pro.bat`.** Le lanceur recherche le
Python d'ArcGIS Pro, puis `py -3` et `python` en secours, puis affiche un menu
numéroté. Il ne lance pas `ArcGISPro.exe` et ne charge aucun add-in.

```text
Assistant ArcGIS Pro : diagnostic, reparation et add-ins
  1) Diagnostic complet, lecture seule (TXT, JSON et HTML)
  2) Corriger le cache ESRI : AP-01, reversible, Pro ferme
  3) Reparer une COPIE d'un projet .aprx (l'original reste intact)
  4) Mettre en quarantaine un add-in precis, de facon reversible
  5) Restaurer un add-in depuis son quarantine.json
  6) Analyser un test deja fait : Pro demarre SANS les add-ins
  7) Aide : dossiers, commandes equivalentes et codes de sortie
  0) Quitter
```

**Entrée** accepte le choix 1, le diagnostic en lecture seule : il ne passe pas
`--apply` et ne modifie rien. Il faut donc répondre explicitement `o` aux
questions des choix 2, 4 et 5, et donner un chemin pour les choix 3 et 5.
Chaque choix affiche d'abord la commande équivalente, puis l'exécute telle
quelle : mêmes contrôles, mêmes refus, mêmes codes de sortie que les options en
ligne de commande. Le menu ne choisit jamais un projet, un mode de réparation
ou un add-in à votre place.

Pour les contrôles de projets récents, ArcGIS Pro doit être fermé ; le
diagnostic du splash screen et des packages de démarrage ne nécessite pas de
l'ouvrir. Fermez Pro avant les choix 2, 3, 4 et 5.

Un lancement sans console interactive (tâche planifiée, redirection, fichier
en entrée) n'affiche pas le menu : le lanceur exécute alors le diagnostic en
lecture seule, pour ne pas attendre une réponse que personne ne peut saisir.

Les rapports **TXT, JSON et HTML autonome** sont créés dans
`%LOCALAPPDATA%\ArcGISPro_PerformanceAnalyser\Rapports`. Le HTML s'ouvre à la
fin ; le TXT est destiné au ticket support et le JSON au diff avec un autre
passage. Pour choisir un autre dossier local :

```bat
Diagnostiquer_ArcGIS_Pro.bat "D:\Diagnostics\Poste"
```

La fenêtre reste ouverte et affiche le dossier ainsi que les erreurs de Python,
de diagnostic ou d'écriture. Les codes 0 à 3 signifient que le diagnostic a
terminé ; 1 et 2 signalent des constats à lire. Le code 64 signale un usage,
une action refusée ou une écriture invalide. **Tant que vous ne choisissez que
1 ou Entrée**, le lanceur ne répare aucun projet et ne modifie ni le registre
ni les add-ins. Si le menu lui-même échoue, le code est 64 et non 1 : la pile
d'appels est affichée pour être transmise, et aucune action n'est lancée.

### Si ArcGIS Pro ne démarre qu'avec `/disableaddins`

C'est le cas le plus fréquent quand « la réparation n'a rien changé » : le
projet, la licence et la géodatabase ne sont pas en cause, le code chargé au
démarrage l'est. Ce test, le logiciel ne peut pas le faire à votre place —
mais il peut l'enregistrer et en tirer le plan d'isolation.

Dans le menu, **choix 6** ; en ligne de commande :

```bat
python arcgis_pro_performance_analyser.py --addins-isolated-ok --report "D:\Diagnostics\Poste" --html
```

Le contrôle des add-ins (PF-31) devient alors un constat de **perte de
données**, et les packages trouvés sont **classés**, avec la raison à côté de
chacun :

- un module défaillant cité par une erreur 1000 est livré par le package ;
- le package a été installé ou mis à jour peu avant le premier plantage daté ;
- sa `desktopVersion` vise une version d'ArcGIS Pro plus ancienne que celle
  installée ;
- il embarque des bibliothèques que Pro charge déjà (`Newtonsoft.Json.dll`,
  `Microsoft.IdentityModel.*`…), ce qui peut faire échouer la résolution
  d'assemblys et faire tomber le runtime .NET.

Le classement est présenté comme une **piste**, jamais comme un verdict :
l'isolation reste un package à la fois, et la phrase qui l'accompagne le dit.
Les étapes générées nomment le premier package à isoler, la commande exacte,
et la marche à suivre si le plantage revient (restaurer, puis passer au
suivant). Un inventaire vide n'est plus un « Conforme » dans ce cas : il
avertit et renvoie vers le gestionnaire des add-ins d'ArcGIS Pro et une
éventuelle configuration gérée.

### Les mêmes actions, sans menu, pour un script

Le menu ne remplace pas les options : il les appelle. Un déploiement, un script
ou une tâche planifiée doit utiliser directement les lignes suivantes, qui ne
changent pas :

```bat
rem Correctif automatisé limité au renommage réversible du cache (AP-01)
Diagnostiquer_ArcGIS_Pro.bat --apply-safe-fixes

rem Réparation du projet dans une nouvelle copie, jamais dans l'original
Diagnostiquer_ArcGIS_Pro.bat --repair-aprx "D:\GIS\Projet.aprx" views
Diagnostiquer_ArcGIS_Pro.bat --repair-aprx "D:\GIS\Projet.aprx" views_history
Diagnostiquer_ArcGIS_Pro.bat --repair-aprx "D:\GIS\Projet.aprx" repack

rem Retrait réversible du chargement d'un package exact affiché par PF-31
Diagnostiquer_ArcGIS_Pro.bat --quarantine-addin "D:\AddIns\Vendor.esriAddinX"

rem Restauration avec le manifeste affiché lors de la quarantaine
Diagnostiquer_ArcGIS_Pro.bat --restore-addin "D:\Quarantine\quarantine-...\quarantine.json"
```

`views` retire seulement l'état des vues au démarrage dans la copie ;
`views_history` retire aussi l'historique de géotraitement dans la copie ;
`repack` reconstruit seulement le conteneur ZIP. Les définitions des cartes et
mises en page ne sont pas supprimées par `views`. Une archive ou un JSON dont
l'intégrité est mauvaise bloque la récupération au lieu de produire une copie
prétendument réparée.

La quarantaine **n'est pas une suppression définitive ni une accusation
automatique**. Elle retire le package choisi des dossiers chargés au démarrage,
tout en conservant ses octets et une commande d'annulation. Elle exige :

- le chemin exact d'un package trouvé par PF-31, et non un fichier arbitraire ;
- la certitude que Pro est fermé ; une liste de processus illisible bloque
  l'action ;
- une copie vérifiée par SHA-256 et taille, avec un manifeste écrit avant le
  retrait de la source ;
- un dossier hors de tout emplacement de chargement par défaut, redirigé ou
  enregistré ;
- pour un package HKLM/machine-wide, l'accord d'un administrateur et l'ajout de
  `--confirm-machine-wide` après le chemin du package.

La restauration vérifie de nouveau l'empreinte et refuse d'écraser un package
qui est réapparu. Elle conserve aussi la sauvegarde de quarantaine. Après une
quarantaine ou une restauration réussie, le `.bat` lance un nouveau diagnostic
et ouvre son HTML. Testez un seul package à la fois ; si le démarrage ne change
pas, restaurez-le avant de tester le suivant.

Aucun de ces modes ne peut garantir de trouver ou réparer **toutes** les pannes
possibles : pilote, installation, profil, matériel, serveur ou corruption CIM
non prise en charge peuvent exiger une autre procédure ou le support Esri.

## Si ArcGIS Pro plante au splash screen, avant tout projet

Un crash à ce stade ne justifie **pas** une récupération de `.aprx` : aucun
projet n'a encore été ouvert. La simple présence de cette toolbox ne lance ni
diagnostic ni correctif au démarrage. Procédez dans cet ordre, sans supprimer
de projet, de géodatabase ou de profil :

1. **Si une ancienne version a appliqué AP-02**, ouvrez le rapport JSON de ce
   passage dans le dossier de sortie choisi. Dans `applied`, recherchez
   l'objet `"id": "AP-02"` et exécutez dans l'invite de commandes la commande
   exacte de sa propriété `undo`. Elle restaure la valeur précédente enregistrée
   au moment du correctif. Le rapport **`.txt` du même passage** affiche aussi
   cette commande après `undo   :`, sans les échappements du JSON : c'est le
   texte le plus simple à copier. Ne remplacez pas cette commande par une
   suppression générique du registre : une préférence antérieure peut devoir
   être restaurée.
2. **Testez sans les extensions tierces**, sans modifier la machine :
   ```bat
   "C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe" /disableaddins
   ```
   Si Pro démarre ainsi, les extensions/configurations de démarrage deviennent
   la piste prioritaire, **sans désigner encore un package coupable**
   ([procédure Esri](https://support.esri.com/en-us/knowledge-base/arcgis-pro-crashes-on-launch-after-installing-add-in-000036367)).
3. **Lancez le diagnostic `.bat` ci-dessus.** PF-31 inventorie les add-ins,
   configurations et plug-ins dans les emplacements par défaut/redirigés et les
   dossiers HKCU/HKLM. Il compare `desktopVersion` à la version de Pro, relève
   `autoLoad`, les assemblies absentes ou les copies d'assemblies Esri, et les
   archives illisibles. DL-13 affiche le module fautif de l'événement
   Application Error 1000 lorsqu'il existe ; sa présence dans un package est un
   indice corrélé, pas une preuve automatique. Le rapport conserve aussi les
   dumps, le GPU/pilote, les profils, la mémoire, le stockage et les autres
   pistes de crash. Pour isoler un package précis, fermez Pro puis utilisez
   `Diagnostiquer_ArcGIS_Pro.bat --quarantine-addin "chemin_exact_du_package"`.
   Relancez Pro normalement. Le manifeste `quarantine.json` et la commande
   `--restore-addin` permettent de revenir en arrière ; ne supprimez pas la
   sauvegarde. Un démarrage réussi après cette isolation est un indice plus
   fort, mais n'exclut pas une interaction ou une seconde cause.
4. **Si le crash persiste**, suivez le
   [soft reset officiel Esri](https://support.esri.com/en-us/knowledge-base/how-to-perform-an-arcgis-pro-soft-reset-000027631) :
   fermez toutes les applications ArcGIS, rendez d'abord toute licence
   concurrente empruntée, puis **renommez** les profils `ArcGISPro` et
   `ArcGISPro.exe_StrongName_*` sous `%APPDATA%\ESRI` et `%LOCALAPPDATA%\ESRI`
   lorsqu'ils existent. Ne les supprimez pas : les copies `_old` permettent de
   restaurer les réglages et favoris nécessaires.
5. **Si Pro plante encore**, relevez dans l'Observateur d'événements Windows le
   module défaillant de l'événement Application Error, la version exacte de Pro
   et l'heure du crash. Ces éléments permettent de distinguer pilote graphique,
   extension, profil et installation ; ne concluez pas à une cause sans cette
   preuve.

## Les deux outils

### Diagnostiquer la lenteur d'ArcGIS Pro

**C'est l'outil à lancer pour analyser tout le poste.**

Lit le poste et écrit `arcgis_pro_performance_analyser_<horodatage>.html`,
`.json` et `.txt` dans le dossier de sortie, puis ouvre le HTML dans le
navigateur par défaut.

**Pour un premier passage, ne remplissez rien** : les valeurs par défaut
suffisent, et aucun hôte ne sera contacté.

| Paramètre | Défaut | Ce qu'il faut mettre |
|---|---|---|
| Dossier de sortie | `<scratch>\ArcGISPro_PerformanceAnalyser` | Rien, ou un dossier **local** comme `C:\Temp\diag`. Jamais un partage réseau |
| Hôte de la géodatabase d'entreprise à sonder | vide | **Vide si vous ne savez pas.** Sinon le serveur nommé dans vos `.sde`, ex. `gisdb01` |
| Port de la géodatabase | 1433 | 1433 SQL Server, 1521 Oracle, 5432 PostgreSQL. Dans le doute, laissez 1433 |
| Hôte du portail à sonder (port 443) | vide | **Vide si vous ne savez pas.** Ex. `gis.monorg.ch` ou `www.arcgis.com` |
| Délai des sondes (ms) | 4000 | Une **borne**, pas une attente : une sonde qui répond en 40 ms rend la main aussitôt |
| Détaillé | décoché | À cocher quand il faut **prouver** un résultat : ajoute la valeur attendue et la ligne brute lue |
| Écrire aussi le rapport texte | coché | Le `.txt` est le format à coller dans un ticket |
| Ouvrir le rapport HTML dans le navigateur | coché | À décocher dans un modèle ModelBuilder |
| Correctifs du poste (indisponibles dans ArcGIS Pro) | **décoché et désactivé** | Paramètre historique conservé pour les modèles ; le diagnostic dans Pro reste toujours en lecture seule |

Chaque paramètre porte une **infobulle** (le point d'interrogation) et un
**paragraphe d'aide** dans le panneau d'aide de l'outil, avec son défaut et
un exemple.

**Le diagnostic exécuté dans ArcGIS Pro ne modifie plus le poste**, même si un
ancien modèle force le paramètre historique `apply_fixes`. Le seul correctif de
poste encore automatisé est **AP-01**, à lancer avec le script Python ou
`Diagnostiquer_ArcGIS_Pro.bat --apply-safe-fixes`, **Pro fermé** : il renomme
le dossier de cache ESRI en lui ajoutant
`.properf-<date>`. **Rien n'est supprimé** et sa commande d'annulation est
inscrite dans le rapport.

**AP-02 est retiré.** Les versions précédentes pouvaient forcer la préférence
« carte graphique hautes performances » pour `ArcGISPro.exe`. Ce changement
pouvait transformer un pilote instable en crash dès le splash screen. PF-11
continue de mesurer et d'expliquer la préférence, mais ni `--apply` ni la
boîte à outils ne la modifient.

Le chemin du HTML est un **paramètre de sortie dérivé** : l'outil peut être
enchaîné dans un modèle ModelBuilder.

### Analyser un projet .aprx externe

**C'est l'outil à lancer pour un projet précis, y compris un projet qui plante à
l'ouverture.** Il ne dépend pas de la liste des projets récents et n'ouvre pas le
projet dans ArcGIS Pro pour l'analyser.

#### Procédure recommandée

1. **Laissez un autre projet ouvert** (ou un projet vide), puis lancez
   **Analyser un projet .aprx externe**. Le fichier correspondant au projet
   actuellement ouvert est refusé ; sélectionnez un autre `.aprx` ou une copie.
2. Dans **Projet .aprx à analyser**, choisissez le fichier externe.
3. Laissez **Diagnostic uniquement — ne créer aucune copie** pour le premier
   passage. Choisissez un dossier de rapports local et exécutez.
4. Lisez le HTML : chaque contrôle présente **la mesure**, **pourquoi elle est
   importante**, **l'impact concret sur ArcGIS Pro**, **les actions numérotées**
   et **la vérification à effectuer**. Les mêmes actions essentielles figurent
   dans les messages de géotraitement pour les anomalies.
5. Si le projet plante parce qu'il rouvre immédiatement une carte ou une mise
   en page problématique, relancez avec **Copie de récupération sans vues au
   démarrage**.
6. Ouvrez la **nouvelle copie** créée à côté de l'original. Elle ne restaure
   pas les anciens onglets. Dans le Catalogue, vérifiez que les cartes et mises
   en page sont toujours présentes, puis rouvrez-les **une par une** pour
   identifier celle qui déclenche le crash.
7. Si l'historique est aussi très volumineux, utilisez **Copie de récupération
   sans vues ni historique**. Conservez l'original pour retrouver les anciens
   paramètres de géotraitement.

#### Ce qui est mesuré

| Contrôle | Question à laquelle il répond |
|---|---|
| PR-01 | Le fichier est-il une archive `.aprx` lisible ? Tous les éléments passent-ils le contrôle CRC ? Y a-t-il des doublons ou du chiffrement ? |
| PR-02 | `GISProject.json` et les autres documents JSON sont-ils présents et valides ? Le format Pro 2.x XML est signalé sans être modifié |
| PR-03 | Le projet, son dossier principal ou sa géodatabase par défaut sont-ils sur un partage ou dans OneDrive ? |
| PR-04 | Les sources fichier locales existent-elles ? Les sources réseau sont identifiées mais **jamais contactées** ; les secrets des chaînes de connexion ne sont pas écrits |
| PR-05 | Quelles cartes, scènes ou mises en page seront restaurées à l'ouverture ? Leur présence est normale et vaut `Info`, pas une fausse anomalie |
| PR-06 | Combien d'éléments d'historique de géotraitement sont stockés et quel est leur poids approximatif ? |
| PF-10, PF-22, PF-09, PF-01, PF-14 | Poids du projet, minuteurs de rafraîchissement, géodatabase par défaut, couches web, reprojections et charge de dessin |

#### Les quatre actions proposées

| Action | Effet | Quand l'utiliser |
|---|---|---|
| **Diagnostic uniquement** | Aucun nouveau `.aprx` ; rapports seulement | Premier passage, ou simple analyse |
| **Reconstruire le conteneur** | Réécrit le ZIP dans une copie, sans changer le contenu des documents | Structure ZIP à reconstruire alors que tous les éléments sont lisibles ; ne répare pas un JSON invalide |
| **Copie sans vues au démarrage** | Retire `views` et `viewLayoutXML` de la copie ; conserve cartes et mises en page | Crash répété pendant la restauration d'une vue enregistrée |
| **Copie sans vues ni historique** | Retire aussi les éléments `GPHistory` de la copie | Même cas, avec historique de géotraitement volumineux |

**Sécurité et limites :**

- l'original n'est **jamais réécrit, renommé ou supprimé** ;
- la copie est créée **à côté de l'original** pour conserver les chemins
  relatifs, avec un nom unique : rien d'existant n'est écrasé ;
- la source est d'abord copiée dans un instantané local temporaire. Si elle
  change pendant la copie, l'analyse est abandonnée ;
- la copie de récupération est rouverte et son intégrité revérifiée avant de
  recevoir son nom final ;
- une archive illisible, un JSON invalide ou un schéma inattendu bloque la
  modification automatique au lieu de produire une copie prétendument réparée ;
- cette récupération ne corrige ni un pilote graphique, ni une extension
  tierce incompatible, ni une géodatabase endommagée, ni toutes les corruptions
  CIM. La vérification finale consiste à ouvrir la copie dans la version cible
  d'ArcGIS Pro et à contrôler toutes les ressources nécessaires ;
- en cas d'erreur CRC ou de document principal invalide, privilégiez une
  sauvegarde/version précédente et transmettez le rapport au support.

Le paramètre dérivé **Projet de récupération créé** retourne le chemin de la
nouvelle copie, ou reste vide si aucune copie n'a été produite.

### L'autotest hors ligne n'est plus dans la boîte à outils

Une version précédente livrait un second outil, « Vérifier l'analyseur ». Il ne
lisait **rien** sur le poste : il vérifiait le *fichier*, en rejouant les
assertions internes du moteur sur des postes de travail de substitution. Le
confondre avec un diagnostic était facile, et cette confusion coûte plus cher
que la garantie ne rapporte : **l'outil a été retiré de la boîte à outils.**

Il reste disponible en ligne de commande, pour le développement :

```
python arcgis_pro_performance_analyser.py --self-test   # 880 assertions
```

Une assertion que la machine ne peut pas lancer est annoncée `SKIP` avec sa
raison, et comptée dans **aucun** des deux nombres : un contrôle non exécuté
n'est pas un contrôle réussi.

> **Défaut corrigé.** Une version précédente lançait ses processus de test avec
> `sys.executable`. Sous ArcGIS Pro, `sys.executable` n'est **pas** `python.exe`
> mais `C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe` : l'autotest ouvrait donc
> une seconde copie d'ArcGIS Pro à l'écran de l'éditrice, laquelle ignorait
> l'argument et ne se terminait jamais — d'où un échec `TimeoutExpired` après
> deux minutes. L'interpréteur est maintenant **résolu par son nom**, et
> l'autotest annonce `SKIP` plutôt que de lancer ce qui n'est pas un
> interpréteur. L'outil de diagnostic n'a jamais été touché : il ne lance que
> `powershell.exe` et `wevtutil.exe`, par leur chemin complet dans `System32`.

## ⚠️ À lire avant d'interpréter un résultat lancé depuis Pro

Lancer le **diagnostic de poste** depuis ArcGIS Pro signifie qu'ArcGIS Pro est
ouvert. Ce n'est pas un détail :

- **DL-12** signale Pro en cours d'exécution — c'est une trouvaille de perte de
  données à part entière, elle remonte dans la liste « act on these first ».
- Les **cinq contrôles qui lisent le fichier projet — PF-01, PF-09, PF-10,
  PF-14 et PF-22 — sont refusés**. Lire un `.aprx` pendant que Pro peut
  l'enregistrer peut faire échouer l'enregistrement : l'outil refuse plutôt que
  de provoquer la panne qu'on lui demande d'expliquer. Chacun répond `Info` avec
  sa raison, jamais un verdict inventé.
- **AP-01** (le correctif de cache) est **refusé** pour la même raison.

Un lancement du diagnostic de poste depuis Pro est donc un rapport poste
**moins** les contrôles automatiques de fichiers récents. Pour ce rapport
complet : fermer Pro et lancer `python arcgis_pro_performance_analyser.py` en
ligne de commande, **dans la session de l'éditrice**, sans élévation.

**Exception :** le second outil, **Analyser un projet .aprx externe**, peut
analyser un autre fichier pendant que Pro reste ouvert. Il refuse le projet
actif et lit un instantané du fichier sélectionné ; il ne contourne pas la
protection avec une ouverture `ArcGISProject` du projet endommagé.

Rien ici ne s'authentifie nulle part et aucun hôte n'est contacté sauf ceux que
vous nommez. Le diagnostic de poste exécuté dans Pro est **toujours en lecture
seule** ; seule la récupération explicitement choisie pour un `.aprx` externe
peut écrire, et elle crée une nouvelle copie sans toucher à l'original.

## Le rapport HTML

Une seule page, autonome, en français :

### Sommaire

Une barre qui reste en haut de page et mène à chaque section, avec le nombre de
contrôles de chaque bloc. Le lien « Perte de données » passe en rouge quand il
contient quelque chose.

### KPI

Sept cartes : **santé globale** (0–100), anomalies, avertissements, trouvailles
de perte de données, contrôles conformes, non déterminés, et **couverture de
mesure**.

Le score coûte 12 points par anomalie et 6 par avertissement — 18 et 9 quand la
trouvaille porte sur des modifications perdues. **Un lancement qui n'a rien pu
mesurer n'a pas de score du tout** : la jauge affiche `n/d`. Un cadran vert sur
un poste illisible serait exactement le défaut que cet outil existe pour
empêcher, déplacé en aval.

### Jauges

Cadres SVG inline : santé globale, couverture de mesure, mémoire libre, disque
libre du pire volume, latence du partage, liaison réseau négociée, taille du
cache ESRI.

**Chaque nombre vient d'un `kpi` que le contrôle a porté**, jamais d'une phrase
relue. Un contrôle qui n'a pas rendu de nombre ne dessine pas de jauge : la page
afficherait sinon un chiffre inventé par le moteur de rendu. `make_check` refuse
un `kpi` qui n'est pas un dictionnaire de nombres — une chaîne ou un booléen est
refusé.

### Mode d'emploi et drapeaux

Une section **Comment lire et corriger** rappelle l'ordre : lire la mesure,
comprendre l'impact, agir une étape à la fois, puis vérifier sur le même
scénario.

Une ligne par symptôme S1 à S6 — dont **crash pendant l'ouverture du projet** —
compte les contrôles en anomalie dont la ligne d'impact nomme ce symptôme, avec
un lien vers chacun. Les six lignes sont toujours là, y compris à zéro : un zéro
doit être visible, pas absent. Rien n'est déduit — un contrôle dont l'impact ne
nomme aucun symptôme n'en drape aucun.

### Chaque contrôle

Une carte avec : le badge de statut, le marqueur « perte de données », le
**problème détecté avec les valeurs mesurées**, **pourquoi le résultat est
important**, l'**impact concret sur ArcGIS Pro**, les **actions à prendre dans
l'ordre** et la **vérification après correction**. Pour `Info` ou `Non mesuré`,
les actions expliquent comment obtenir la mesure au lieu d'inventer une panne.
La valeur attendue, la preuve et les détails techniques sont repliés. Les
nombres mesurés apparaissent en pastilles, pour vérifier la jauge contre la
mesure.

### Le reste

Les portes des correctifs, la commande d'annulation de tout ce qui a été
appliqué, la légende des cinq statuts, et le document JSON embarqué pour le
diff. Un en-tête `Content-Security-Policy` n'autorise aucune source externe ;
mode sombre et feuille de style d'impression inclus.

### Sécurité du rendu

Le rapport est plein de chaînes fournies **par la machine**, pas par
l'opérateur : noms de comptes, chemins sur un partage, descriptions de cartes
réseau, chemins d'exclusion Defender, texte du journal d'événements. Un nom de
dossier sur un serveur de fichiers peut donc contenir du balisage.

Le moteur de rendu échappe systématiquement tout ce qu'il écrit, et le bloc JSON
embarqué encode `<`, `>` et `&` en `\u003c` / `\u003e` / `\u0026` : aucune
chaîne du rapport ne peut être lue comme du balisage, et aucune ne peut fermer
l'élément `<script>` en avance. Le JSON se décode toujours à l'identique.
`tests/test_toolbox.py` le vérifie sur un document où chaque champ est un
payload.

## Sources

**Chaque contrôle du rapport cite ses sources**, dans un bloc « Sources » placé
sous sa propre correction. Ce ne sont pas des notes de bas de page réunies à la
fin : la référence est à côté du problème qu'elle appuie, pour que vous puissiez
la vérifier avant d'agir. 39 des 40 contrôles en portent ; **DL-00** (qui lance
l'outil) n'en a pas, parce qu'aucune source honnête ne le concerne — et un
contrôle sans source honnête n'en reçoit pas d'inventée.

Ce sont des **liens de navigation** : la page du rapport ne télécharge rien
depuis ces hôtes, elle s'ouvre donc aussi bien sur un poste isolé du réseau.

**Là où Esri publie un chiffre, c'est le chiffre d'Esri qui est utilisé et le
rapport le dit.** Les prérequis officiels (processeur, mémoire, stockage, carte
graphique) sont repris tels quels, avec leurs trois paliers — minimum,
recommandé, optimal — pour que vous sachiez si vous êtes en dessous du minimum
ou seulement en dessous du recommandé.

Les autres seuils restent **ceux de cet outil** : des tolérances choisies pour
décider « conforme / à surveiller » sur des points qu'Esri ne chiffre pas
(latence réseau, âge d'un pilote, poids d'une géodatabase). Le rapport distingue
les deux : une ligne cite Esri, l'autre dit « tolérance de cet outil ».

Ce qui suit est ce qui a été consulté pour écrire les pistes de correction, avec
ce que chacun apporte réellement ici.

### Documentation Esri

| Source | Ce qu'on y prend |
|---|---|
| [Configuration système requise pour ArcGIS Pro](https://doc.esri.com/fr/arcgis-pro/latest/get-started/arcgis-pro-system-requirements.html) — **chiffres publiés par Esri** (3.7, et la version 3.6 en anglais) | **Processeur** : minimum 2 cœurs avec multithreading simultané, recommandé 4, optimal 10 (**PF-28**). **Mémoire** : minimum 8 Go, recommandé 32, optimal 64 (**PF-12**). **Stockage** : 32 Go libres au minimum, sur SSD en recommandé, et le cache de visualisation seul peut prendre jusqu'à 32 Go (**PF-13**, **PF-06**). **Carte graphique** : discrète (non intégrée) recommandée, 8 Go de mémoire dédiée, et « utilisez le dernier pilote disponible » (**PF-29**, **PF-11**). **OneDrive et Google Drive ne sont pas pris en charge** (**DL-05**, **DL-06**, **DL-07**). Certains outils de géotraitement exigent un processeur AVX, et la recherche d'outils exige AVX2 |
| [Gérer les add-ins](https://pro.arcgis.com/en/pro-app/latest/get-started/manage-add-ins.htm), [emplacements et ordre de chargement](https://pro.arcgis.com/en/pro-app/3.6/sdk/api-reference/conceptdocs/docs/ProConcepts-Advanced-Topics.html), [diagnostic des add-ins](https://pro.arcgis.com/en/pro-app/3.6/sdk/api-reference/conceptdocs/docs/ProGuide-Diagnosing-ArcGIS-Pro-Add-ins.html) | Dossiers bien connus par défaut/redirigés et HKCU/HKLM, configurations et plug-ins, chargement `autoLoad`, règles `desktopVersion` et `defaultAssembly` (**PF-31**) ; `/disableaddins` comme test d'isolement, sans identifier à lui seul le package responsable |
| [Settings that affect data access performance](https://doc.esri.com/en/arcgis-pro/latest/get-started/layer-map-project-perf-optimization.html) — documentation officielle ArcGIS Pro 3.7 | Reprojection à la volée quand les données ne sont pas dans la même référence spatiale (PF-01) ; coût des jointures et des requêtes de définition non sélectives (PF-14) ; poids des étiquettes et des `feature weights` (PF-14) ; intérêt des plages d'échelle de visibilité |
| [Troubleshooting Performance Issues in ArcGIS Pro](https://community.esri.com/t5/arcgis-pro-documents/troubleshooting-performance-issues-in-arcgis-pro/ta-p/1025516) — Esri Community | Pilote de carte graphique à prendre chez le fabricant ; antivirus à écarter temporairement pour isoler la cause (PF-17) ; `WARP` dans le readout = Pro émule la carte graphique sur le processeur ; historique de géotraitement stocké dans le `.aprx` (PF-10) ; ne garder ouverts que les cartes et mises en page utiles (PF-10) |
| [Why is ArcGIS Pro so slow?](https://community.esri.com/t5/arcgis-pro-questions/why-is-arcgis-pro-so-slow/td-p/508005/page/5) — Esri Community | Renommer les dossiers `ESRI` de `AppData\Local` et `AppData\Roaming` pour tester le cache (AP-01) ; autoriser `ArcGISPro.exe` dans le pare-feu Windows Defender ; latence réseau et données locales contre données en partage |

### Forums et discussions de praticiens

La documentation Esri dit quoi régler. Les fils ci-dessous disent **ce qui
tombe vraiment en panne** et dans quel ordre les gens l'ont trouvé — ce sont eux
qui ont fait ajouter trois contrôles à cet outil.

| Source | Ce qu'on y prend |
|---|---|
| [r/gis — « is there an actual reason ArcGIS is so painfully slow? »](https://www.reddit.com/r/gis/comments/t7lc3h/not_being_funny_is_there_an_actual_reason_arcgis/) | **ArcGIS Pro est mono-thread** : « vous pouvez avoir 16 cœurs et 32 threads, Pro tournera sur un seul ». C'est ce qui justifie le contrôle **PF-28** : l'horloge d'un seul cœur compte plus que le nombre de cœurs |
| [r/ArcGIS — OneDrive et les fichiers SIG](https://www.reddit.com/r/ArcGIS/comments/1srr0qm/one_drive_and_gis_files_question/) | « plus de 10 minutes pour ouvrir un seul projet » ; garder les données hors d'OneDrive et de SharePoint, la sauvegarde seule est acceptable ; **une géodatabase mobile a beaucoup moins de fichiers à synchroniser** qu'une géodatabase fichier (DL-05, DL-06, PF-30) |
| [r/ArcGIS — Pro ralentit à chaque modification](https://www.reddit.com/r/ArcGIS/comments/1ni4key/arcgis_pro_slowing_down_with_each_edit/) | l'**indexation** est active par défaut et ralentit au lieu d'accélérer (PF-08) ; une géodatabase sur un **NAS** ramenée en local a « quasiment réglé » le problème (PF-09, PF-18) ; cadre de carte dans la même projection que les données (PF-01) |
| [r/gis — Pro plante sur les gros jeux de données](https://www.reddit.com/r/gis/comments/htmscu/arcgis_pro_crashing_when_editing_large_data_sets/) | utiliser une géodatabase fichier **locale** plutôt qu'une ressource réseau ; un shapefile d'environ 2 Go par classe d'entités est un problème en soi — passer les grosses classes en géodatabase locale (PF-30) |
| [r/ArcGIS — carte lente à charger](https://www.reddit.com/r/ArcGIS/comments/1v82j68/arcgis_pro_slow_loading_map/) | JPEG géoréférencés de ~28 Go chacun (PF-16) ; tirer les données depuis SharePoint ajoute de la latence ; **copier en local** et travailler sur une géodatabase de travail a « amélioré les performances de façon spectaculaire » |
| [r/ArcGIS — traitement bloqué en « Pending »](https://www.reddit.com/r/ArcGIS/comments/1kix8v8/how_much_time_is_likely_left_on_this_process/) | « Pending » = OneDrive essaie de téléverser ; le dossier **Documents** est synchronisé en permanence ; couper la synchronisation puis rouvrir la carte donne une différence immédiate (DL-06) |
| [r/gis — « it just shuts with no warning and no saving »](https://www.reddit.com/r/gis/comments/940cyk/arcgis_pro_performance_impressions/) | la fermeture sans avertissement ni enregistrement, sur des fichiers en réseau — le symptôme que **DL-13** va chercher dans le journal d'événements |

Ces pages ont été **relues lors de cette modification**. Elles ne sont pas
citées dans le code : les seuils chiffrés de l'outil restent les siens, et le
rapport le dit.

## Vérifier

```
python tests/test_toolbox.py      # 156 assertions : rendu, deux outils, sécurité dans Pro et récupération .aprx
python arcgis_pro_performance_analyser.py --self-test   # 880 assertions sur le diagnostic
python build_toolbox.py --check   # le .pyt livré est bien ce que ses sources produisent
```

`tests/test_toolbox.py` n'a besoin que de la bibliothèque standard : il exécute
le `.pyt` contre un module `arcpy` de substitution, donc ce sont bien
`getParameterInfo`, `updateMessages` et `execute` des deux outils qui tournent.
Il crée aussi de vrais `.aprx` temporaires, vérifie les CRC, compare l'original
octet par octet et contrôle que les copies sans vues/historique conservent les
définitions des cartes et mises en page.

Un détail que ce test épingle : le `.pyt` embarque **sa propre copie** du moteur.
Remplacer `run()` dans le module importé séparément ne change donc rien à ce que
fait l'outil — le test remplace la fonction dans le `.pyt`, et vérifie que les
deux ne sont pas le même objet, pour qu'un futur test ne puisse pas passer à
côté en patchant la mauvaise copie.

## Confidentialité

Le rapport contient le nom du compte, des chemins locaux, des noms de fichiers
de connexion (les noms seulement, jamais le contenu) et des chemins d'exclusion.
**Le relire avant de l'envoyer à qui que ce soit.**
