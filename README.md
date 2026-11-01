# FactureQR

Logiciel de facturation suisse pour indépendants et petites structures du spectacle : factures avec **bulletin QR**, devis, location de matériel avec inventaire, envoi par e-mail. Tout tourne **en local** sur votre ordinateur (Python + navigateur) : vos données ne quittent pas votre machine, sauf vers le dossier de sauvegarde que vous choisissez.

## Fonctionnalités

### Facturation
- Coordonnées de l'entreprise, logo, IBAN ou QR-IBAN, TVA optionnelle (activable dans les réglages)
- Clients avec **plusieurs tarifs horaires chacun** (montage, régie, démontage…) et recherche insensible aux accents
- Une facture en trois choix : **client → événement ou non → heures, forfait / location, ou les deux**
- Heures saisies avec **date, début et fin** (calcul automatique, passage de minuit géré) et **annexe « Décompte des heures »** en option
- PDF soigné avec **bulletin QR suisse** conforme (SIX / ISO 20022), référence QRR ou SCOR générée automatiquement
- Factures sur **plusieurs pages** (« À reporter / Report », « Page x / N »), lignes de hauteur identique
- Afficher ou non l'échéance et les modalités de paiement (réglage général, modifiable par facture)
- **Factures antidatées**, **numérotation séquentielle ou aléatoire**, numéro modifiable sur chaque facture

### Devis
- Numérotation séparée (D-2026-001), **durée de validité** (15 / 30 / 60 / 90 jours ou date au choix), statut « Expiré » automatique
- PDF sans bulletin QR, avec conditions et bloc **« Bon pour accord »** à signer
- **Transformation en facture** en un clic (mêmes lignes, ajustables), liens entre devis et facture

### Location et inventaire
- **Inventaire** : stock, catégories, référence / n° de série, prix de location par jour, valeur à neuf (assurance), export CSV
- Bouton **« + Matériel de l'inventaire »** dans les factures et les devis : recherche, filtre par catégorie, quantités, dates de location et disponibilité affichée pour chaque article
- Depuis l'inventaire, **cochez des articles puis « Facturer la sélection »** (ou « Faire un devis ») ; depuis la fiche d'un article, « Facturer cet article »
- Lignes de location avec **dates** et nombre de jours (montant = qté × jours × prix)
- **Disponibilité en direct** pendant la saisie et par période : réservé (factures, devis acceptés), en option (devis envoyés), disponible
- Fiche article avec réservations à venir et historique des locations
- **PDF de logistique** :
  - **Liste de chargement** : cases « Préparé » et « Chargé », quantités, articles groupés par catégorie, signatures (préparé par, chargé / livré par, reçu par le client)
  - **Bon de retour** : quantité sortie, case « Rentré » à remplir, case OK, état / remarques, signatures
  - Depuis une facture ou un devis, ou **pour une période** depuis l'inventaire (tous les départs ou retours du jour, un document par page)

### E-mail, archives, sauvegarde
- Envoi par **e-mail** avec le PDF en pièce jointe (aucun lien à télécharger), mise en page sobre, **couleurs personnalisables** avec aperçu en direct, textes distincts pour factures et devis
- Archive filtrable (client, année, statut, recherche), statut payé / en retard, **export CSV** pour la comptabilité
- Suppression une à une ou par sélection, avec confirmation renforcée pour les documents déjà envoyés
- **Sauvegarde automatique** après chaque modification dans un dossier synchronisé (Google Drive, iCloud, OneDrive, Dropbox, kDrive) avec historique quotidien

## Installation

1. Installez **Python 3.10 ou plus récent** : https://www.python.org/downloads/
   (Windows : cochez « Add Python to PATH » pendant l'installation).
2. Récupérez le code :
   ```bash
   git clone https://github.com/kazenotodoroki/facture-qr.git
   ```
   ou, sans git : bouton **Code → Download ZIP** sur GitHub, puis dézippez.

Les dépendances (`flask`, `qrbill`, `reportlab`, `svglib`) s'installent automatiquement au premier lancement.

## Lancement

- **Windows** : double-cliquez sur `Demarrer_Windows.bat`
- **Mac** : double-cliquez sur `Demarrer_Mac.command` (la première fois : clic droit → Ouvrir si macOS bloque le fichier)
- En terminal :
  ```bash
  pip install -r requirements.txt
  python app.py
  ```

Le navigateur s'ouvre sur http://127.0.0.1:5000. Laissez la fenêtre de terminal ouverte pendant l'utilisation ; la fermer arrête le logiciel.

## Mise à jour

1. Fermez FactureQR (fenêtre de terminal).
2. Dans le dossier du projet :
   ```bash
   git pull
   ```
   (ou téléchargez le nouveau ZIP et copiez-le par-dessus, **sans supprimer le dossier `data`**).
3. Relancez. La base de données se met à jour toute seule ; la version s'affiche en bas du menu.

## Premiers pas

1. **Paramètres → Entreprise** : nom, adresse, IBAN, logo, TVA, numérotation, réglages des devis.
2. **Paramètres → Envoi e-mail** : serveur SMTP (préréglages Infomaniak, Gmail, Outlook, Bluewin, Hostpoint), puis « Tester la connexion ». Avec Gmail, utilisez un *mot de passe d'application* (validation en deux étapes requise).
3. **Paramètres → Apparence** : couleurs et textes de l'e-mail et du PDF.
4. **Paramètres → Sauvegarde** : chemin d'un dossier de votre Google Drive (ex. `G:\Mon Drive\Factures`).
5. **Clients** : ajoutez vos clients et leurs tarifs.
6. **Inventaire** (si vous louez du matériel) : ajoutez vos articles ; ils s'ajoutent ensuite à une facture avec le bouton « + Matériel de l'inventaire ».
7. **+ Nouvelle facture** ou **+ Nouveau devis**.

## Données et confidentialité

Les données sont stockées dans le dossier `data/` du projet :

| Emplacement | Contenu |
|---|---|
| `data/factures.db` | base SQLite : entreprise, réglages, clients, factures, devis, inventaire |
| `data/pdf/AAAA/` | PDF des factures et devis, classés par année |
| `data/logo/` | votre logo |

Le dossier `data/` est **exclu de git** (`.gitignore`) : vos clients, montants et le mot de passe SMTP ne sont jamais publiés sur GitHub. Le mot de passe SMTP est stocké en clair dans la base locale ; protégez l'accès à votre ordinateur.

Dans le dossier de sauvegarde : copie de la base, `historique/` (une copie par jour, 30 jours par défaut) et tous les PDF. Pour restaurer sur un autre ordinateur, copiez `factures.db` dans `data/` et le dossier `PDF` dans `data/pdf/`.

## Structure du projet

```
app.py           application Flask (routes, logique)
db.py            schéma SQLite, migrations automatiques, numérotation
pdfgen.py        PDF factures et devis (bulletin QR via qrbill)
packlist.py      PDF liste de chargement et bon de retour
inventory.py     calcul des réservations et de la disponibilité
mailer.py        e-mail HTML et envoi SMTP
references.py    références de paiement QRR / SCOR
backup.py        sauvegarde automatique
templates/       pages HTML (Jinja)
static/          feuille de style
fonts/           police Inter (licence SIL OFL, voir fonts/LICENSE.txt)
```

## Bon à savoir

- **Numérotation** : 2026-001, 2026-002… ou aléatoire (2026-483920), préfixe optionnel ; devis D-2026-001. Un numéro saisi à la main est vérifié (pas de doublon).
- **Supprimer ou annuler ?** Pour la comptabilité, préférez le statut « Annulée » pour une facture déjà envoyée ; la suppression reste possible après confirmation.
- **Régénérer le PDF** applique le logo, les couleurs et les coordonnées actuels à un document existant.
- **Références QR** : avec un QR-IBAN (IID 30000–31999), référence QR à 27 chiffres ; avec un IBAN normal, référence créancier RF. Votre banque rapproche ainsi les paiements automatiquement.
- **Disponibilité du matériel** : seules les lignes avec un article et des dates de location sont comptées. Un devis transformé en facture n'est pas compté deux fois.
