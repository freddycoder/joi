# joi

Prérequis: python3.4 ou +

## Installation
Sous windows dans un invite de commande avec les privilège d'administrateur, executer les commande si-dessous.
```
python -m pip install pymongo
pip install gTTS
pip install playsound
pip install beautifulsoup4
pip install requests
```
Installer mongodb serveur sur le poste de travail local à la location par défaut.
https://www.mongodb.com/download-center?jmp=nav#enterprise

Pour lancer mongodb serveur vous pouvez aller dans un invite de commande, dans le dossier bin de mongodb et tapez mongo.exe.

Pour plus d'information visité la documentation officiel:
https://docs.mongodb.com/manual/tutorial/install-mongodb-enterprise-on-windows/

## Structure du programme

Pour commencer le programme importe les librairies. Ensuite il lit tout les classe et les fonction à l'intérieur. 
Puis il arrive à dernière ou c'est écrit Joi.reveille_toi(). Donc dans la classe Joi faire la fonction reveille_toi.
```
choice = input(Joi.dit("Choisit une fonction"))
if str(choice) == "math":
    Skills.faire_des_math()
elif str(choice) == "nouvelle":
    Skills.nouvelle_economique()
elif str(choice) == "produit_croise":
    Skills.produit_croise()
elif str(choice) == "fait_jouer":
    Skills.fait_jouer()
elif str(choice) == "va_dormir":
    quit()
else:
    print("Vous avez entrer une fonction qui n'existe pas")
```                

À cet instant l'utilisateur doit entré une fonction. Un action que Joi va executer. Tout les "Skills" sont dans une classe appart.
Dans la classe Joi, il y a présentement seulement 2 fonction. Soit reveille_toi() et dit(text). Donc pour faire dire quelque chose
à Joi, il faut seulement tapez: Joi.dit("quelque chose"). Pour éviter qu'il y ait trop de requête au serveur de gTTS, la première fois
que quelque chose est prononcé, un fichier est enregistrer dans le dossier voix et dans la base de donnée. Donc si une texte 
doit être transformer en voix pour une deuxième fois, il va être lut sur l'ordinateur local sans passer par le service 
Google-Text-to-speech.

Deux des skills sont issue de deux scripts que j'ai créer précédament, donc il est facile d'insérer d'ajouter des skills.
Il suffit de créer une fonctiont dans la class Skills. Puis de créer une condition dans la classe Joi, la fonction reveille_toi.
