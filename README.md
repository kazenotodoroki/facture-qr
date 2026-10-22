# FactureQR

Logiciel de facturation QR suisse, en local sur votre ordinateur (Python).

- Coordonnées de l'entreprise, logo, IBAN / QR-IBAN, TVA (optionnelle)
- Clients avec **plusieurs tarifs horaires chacun** (montage, régie…)
- Facture en 3 choix : **client → événement ou non → heures, forfait / location, ou les deux sur la même facture**
- **Recherche de clients** (insensible aux accents) dans la liste et lors de la création d'une facture
- Heures saisies avec **date, début et fin** (calcul automatique, passage de minuit géré), et **annexe « Décompte des heures »** en option
- Lignes de hauteur identique, factures sur **plusieurs pages** (« À reporter / Report », « Page x / N », bulletin QR sur la dernière page de la facture)
- Réglages généraux : **afficher ou non la TVA**, **afficher ou non l'échéance**, **modalités de paiement** automatiques ou non (modifiables par facture)
- PDF élégant avec **bulletin QR suisse** conforme (SIX / ISO 20022), référence QRR ou SCOR automatique
- Envoi par **e-mail** (PDF en pièce jointe, aucun lien à télécharger), page centrée aux angles droits avec signature et logo, **couleurs personnalisables** avec aperçu en direct
- Archive filtrable (client, année, statut, recherche), statut payé / en retard, **export CSV** pour la compta
- **Suppression** de factures (une à une ou par sélection, confirmation renforcée pour les factures envoyées)
- **Factures antidatées** (échéance calculée depuis la date choisie) et **numérotation séquentielle ou aléatoire**, numéro modifiable sur chaque facture
- **Devis** : numérotation séparée (D-2026-001), durée de validité (15/30/60/90 jours ou date au choix), statut « Expiré » automatique, bloc « Bon pour accord », envoi par e-mail et **transformation en facture** en un clic
- **Sauvegarde automatique** dans un dossier Google Drive (ou iCloud, OneDrive, Dropbox, kDrive)

## Installation (une seule fois)

1. Installez Python 3.10 ou plus récent : https://www.python.org/downloads/
   (Windows : cochez « Add Python to PATH » pendant l'installation).
2. Dézippez le dossier `factureqr` où vous voulez (par ex. Documents).

## Lancement

- **Windows** : double-cliquez sur `Demarrer_Windows.bat`
- **Mac** : double-cliquez sur `Demarrer_Mac.command`
  (la 1ʳᵉ fois : clic droit → Ouvrir, si macOS bloque le fichier)
- Ou en terminal : `pip install -r requirements.txt` puis `python app.py`

Le navigateur s'ouvre sur http://127.0.0.1:5000. Laissez la fenêtre noire ouverte pendant l'utilisation.

## Premiers pas

1. **Paramètres → Entreprise** : nom, adresse, IBAN, logo.
2. **Paramètres → Envoi e-mail** : serveur SMTP (boutons préréglés Infomaniak, Gmail, Outlook…), puis « Tester la connexion ».
3. **Paramètres → Apparence** : couleurs de l'e-mail et du PDF, texte du message.
4. **Paramètres → Sauvegarde** : chemin d'un dossier de votre Google Drive.
5. **Clients → Nouveau client** avec ses tarifs.
6. **+ Nouvelle facture**.

## Où sont mes données ?

- `data/factures.db` : base de données (entreprise, clients, factures)
- `data/pdf/AAAA/` : tous les PDF, classés par année
- Dans le dossier de sauvegarde : copie de la base, historique quotidien et tous les PDF

Pour restaurer sur un nouvel ordinateur : copiez `factures.db` (depuis le Drive) dans `data/`, et le dossier `PDF` dans `data/pdf`.

## Bon à savoir

- Numérotation automatique par année : 2026-001, 2026-002… ou aléatoire (2026-483920), préfixe optionnel. Le numéro peut aussi être saisi à la main ; les doublons sont refusés.
- Une facture envoyée ne peut plus être supprimée : passez-la en « Annulée » (traçabilité comptable).
- « Régénérer le PDF » applique les couleurs, logo et coordonnées actuels à une ancienne facture.
- Avec un **QR-IBAN** (IID 30000-31999), une référence QR à 27 chiffres est créée ; avec un IBAN normal, une référence créancier RF. Votre banque peut ainsi rapprocher les paiements automatiquement.
