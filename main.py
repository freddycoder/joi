""" Database """
import pymongo
from pymongo import MongoClient
""" Math """
import random
""" Text-to-Speech """
from gtts import gTTS
from playsound import playsound
import uuid
''' Webscrapping'''
from bs4 import BeautifulSoup as soup
from urllib.request import urlopen as uReq

class Joi:
    def reveille_toi():
        reveiller = True
        while reveiller == True:
            choice = input(Joi.dit("Choisit une fonction"))
            if str(choice) == "math":
                Skills.faire_des_math()
            elif str(choice) == "nouvelle":
                Skills.nouvelle_economique()
            elif str(choice) == "aller_dormir":
                quit()
            else:
                print("Vous avez entrer une fonction qui n'existe pas")

    def dit(text):
        client = MongoClient()
        db = client.Joi_brain
        memoire_vocal = db.memoire_vocal
        try:
            if text ==  memoire_vocal.find_one({"text": text})["text"]:
                file = memoire_vocal.find_one({"text": text})["path"]
                playsound(file)
        except:
            file = "./Voice/" + str(uuid.uuid4()) + ".mp3"                      #il y a une possibilité, bien quelle soit très mince, que un jour uuid créé un nom de fichier pareil au précédent. Ce qui va causer un sacré casse tête...
            parole = {"text": str(text), "path": str(file)}
            nouvelle_id = memoire_vocal.insert_one(parole).inserted_id
            tts = gTTS(text=text, lang="fr")
            tts.save(file)
            playsound(file)
        return ""
        
class Skills:
    def nouvelle_economique():
        ''' Requête vers la page d'acceuil pour aller chercher l'article le plus récent. '''
        site="http://ici.radio-canada.ca/economie/gerald-fillion"
        uClient = uReq(site)
        code = uClient.read()
        uClient.close()
        page_soup = soup(code, "html.parser")
        site = page_soup.h3.find('a').get('href')

        ''' Récuppérer les information de l'article le plus récent. '''
        uClient = uReq(site)
        code = uClient.read()
        uClient.close()
        ''' Variable code qui resort. '''

        ''' Enregistre le code de la page dans la variable page_soup '''
        page_soup = soup(code, "html.parser", from_encoding="utf-8")
        '''  Enregistre le titre dans la variable titre '''
        titre = str(page_soup.h1)
        titre = titre.replace("<h1>", "")
        titre = titre.replace("</h1>", "")
        titre = titre.replace("\xa0", " ")
        titre = titre + ". "

        ''' Prend le texte de l'article et nettoye le. '''
        code_texte = page_soup.find("div", { "data-component-name" : "DocumentNewsStoryBody" })
        texte_non_clean = code_texte.get_text()
        presque = texte_non_clean.strip()
        mieux = presque.replace("\xa0", "")
        encore_mieux = mieux.replace("\n", " ")
        on_y_est = encore_mieux.replace("\u2019", "'")
        ''' Le texte est maintenant propre '''
        ''' Il en resort la variable on_y_est '''

        ''' Aller chercher la date dans le code de la page '''
        date = page_soup.find("time")

        """ Initialise la base de donnée """
        client = MongoClient()
        db = client.Nouvelle_Economique
        Nouvelles = db.Nouvelles
        """ Essaie de trouver un article avec la même date dans la DB"""
        try:
            date_query = Nouvelles.find_one({'Date': str(date)}, {"Date": 1})
            if date_query.get("Date") == str(date):
                Joi.dit("Il n'y a pas de nouvel article, aimerais tu quand même que je lise l'article précédent?")
                user_Joi = str(input("Oui ou non?"))
                if user_Joi == "oui":
                    Joi.dit(str(titre) + " " + str(on_y_est))
                else:
                    Joi.dit("Ok")
        except:
            Joi.dit("Il y a un nouvel article!")
            nouvelle = {"Date": str(date), "Titre": str(titre), "Contenue": str(on_y_est)}
            nouvelle_id = Nouvelles.insert_one(nouvelle).inserted_id
            """ Ok alors l'information est bien enregistrer dans la base de donnée """
            Joi.dit(str(titre) + ". " + str(on_y_est))

        Joi.dit("Aimerais-tu que je te lise l'article suggéré?")
        user_interaction = str(input("Lire l'article suggéré? Oui ou non?\n"))

        if user_interaction == "oui":
            """ Plus de nouvelle? À lire aussi! """
            a_lire_aussi = code_texte.find("div", { "class" : "framed" } )
            autre_nouvelle = a_lire_aussi.p.find('a').get('href')
            uClient = uReq("http:" + autre_nouvelle)
            code = uClient.read()
            uClient.close()
            ''' Prend le texte de l'article et nettoye le. '''
            code_texte = page_soup.find("div", { "data-component-name" : "DocumentNewsStoryBody" })
            texte_non_clean = code_texte.get_text()
            presque = texte_non_clean.strip()
            mieux = presque.replace("\xa0", "")
            encore_mieux = mieux.replace("\n", " ")
            on_y_est = encore_mieux.replace("\u2019", "'")
            ''' Le texte est maintenant propre '''
            ''' Il en resort la variable on_y_est '''
            """ Section Text-to-Speech """
            Joi.dit(str(titre) + " " + str(on_y_est))
        else:
            Joi.dit("Ok")

    def faire_des_math():
        run = True
        bonne_reponse = 0
        question_total = 0
        print("Entrainement mathématique:")
        print("Pour quitté tapper x\n")
        liste_des_operations = ["division", "multiplication", "soustraction", "addition", "carre", "racine"]
        while run:
            operation = random.choice(liste_des_operations)

            if operation == "division":
                num1=random.randrange(-13, 13)
                num2=random.randrange(-13, 13)
                while num1 == 0:
                    num1=random.randrange(-13, 13)
                while num2 == 0:
                    num2=random.randrange(-13, 13)
                reponse=num1*num2

                print(str(reponse) + " / " + str(num2))
                #Vérifier si le premier nombre est négatif pour la bonne prononciation de Joi
                if reponse < 0:
                    Joi.dit("Diviser moins " + str(abs(reponse)) + " par " + str(num2) + "?")
                else:
                    Joi.dit("Diviser " + str(reponse) + " par " + str(num2) + "?")

                tentative=input("Réponse : ")
                if tentative == "x":
                    run = False

                elif int(num1)==int(tentative):
                    print("Bonne réponse")
                    Joi.dit("Bonne réponse")
                    bonne_reponse += 1
                    question_total += 1
                else:
                    Joi.dit("Mauvaise réponse")
                    question_total += 1

            elif operation == "multiplication":
                num1=random.randrange(-13, 13)
                num2=random.randrange(-13, 13)
                reponse=num1*num2

                print(str(num1) + " x " + str(num2))
                #Text to speech section
                if num1 < 0:
                    Joi.dit("Multiplier moins" + str(num1) + " par " + str(num2) + "?")
                else:
                    Joi.dit("Multiplier " + str(num1) + " par " + str(num2) + "?")
                
                tentative=input("Réponse : ")

                if tentative == "x":
                    run = False

                elif int(reponse)==int(tentative):
                    print("Bonne réponse")
                    Joi.dit("Bonne réponse")
                    bonne_reponse += 1
                    question_total += 1
                else:
                    print("Mauvaise réponse")
                    Joi.dit("Mauvaise réponse")
                    question_total += 1

            elif operation == "soustraction":
                num1=random.randrange(-99, 99)
                num2=random.randrange(-99, 99)
                reponse=num1-num2

                print(str(num1) + " - " + str(num2))
                #Text to speech section
                if num1 < 0:
                    Joi.dit("Soustraire " + str(num1) + " de " + str(num2) + " ?")
                else:
                    Joi.dit("Soustraire" + str(num1) + " de " + str(num2) + " ?")

                tentative=input("Réponse : ")

                if tentative == "x":
                    run = False

                elif int(reponse)==int(tentative):
                    print("Bonne réponse")
                    Joi.dit("Bonne réponse")
                    bonne_reponse += 1
                    question_total += 1
                else:
                    print("Mauvaise réponse")
                    Joi.dit("Mauvaise réponse")
                    question_total += 1

            elif operation == "addition":
                num1=random.randrange(-99, 99)
                num2=random.randrange(-99, 99)
                reponse=num1+num2

                print(str(num1) + " + " + str(num2))
                #Text to speech section
                if num1 < 0:
                    Joi.dit("Additionner " + str(num1) + " de " + str(num2) + "?")
                else:
                    Joi.dit("Additionner" + str(num1) + " de " + str(num2) + "?")

                tentative=input("Réponse : ")

                if tentative == "x":
                    run = False

                elif int(reponse)==int(tentative):
                    print("Bonne réponse")
                    Joi.dit("bonne_reponse")
                    bonne_reponse += 1
                    question_total += 1
                else:
                    print("Mauvaise réponse")
                    Joi.dit("Mauvaise réponse")
                    question_total += 1

            elif operation == "carre":
                num1=random.randrange(2, 20)
                carre=num1*num1
                print("La racine carré de " + str(carre))
                #Speech to text section
                Joi.dit("La racine carré de " + str(carre) + "?")
                tentative=input("Réponse : ")

                if tentative == "x":
                    run = False

                elif int(num1)==int(tentative):
                    print("Bonne réponse")
                    Joi.dit("Bonne réponse")
                    bonne_reponse += 1
                    question_total += 1
                else:
                    print("Mauvaise réponse")
                    Joi.dit("Mauvaise réponse")
                    question_total += 1

            elif operation == "racine":
                num1=random.randrange(2, 20)
                carre=num1*num1
                print(str(num1) + " au carré")
                #Speech to text section
                Joi.dit(str(num1) + " au carré" + "?")

                tentative=input("Réponse : ")

                if tentative == "x":
                    run = False

                elif int(carre)==int(tentative):
                    print("Bonne réponse")
                    Joi.dit("Bonne réponse")
                    bonne_reponse += 1
                    question_total += 1
                else:
                    print("Mauvaise réponse")
                    Joi.dit("Mauvaise réponse")
                    question_total += 1
            else:
                Joi.dit("J'ai un problème, je suis incapable d'identifier le type d'operation...")

        print("Tu as eu " + str(bonne_reponse) + " bonne réponse sur " + str(question_total) + " questions.")
        Joi.dit("Tu as eu " + str(bonne_reponse) + " bonnes réponses sur " + str(question_total) + " questions.")
        if 100*bonne_reponse/question_total < 60:
            print("Tu n'aurais même pas eu la note de passage.")
            Joi.dit("Tu n'aurais même pas eu la note de passage.")
        elif bonne_reponse == question_total:
            print("Waouw! Tu eu les bonnes réponses pour tous les questions!")
            Joi.dit("Waouw! Tu as eu la bonne réponse pour toutes les questions!")
        else:
            print("Bravo! Tu as passer le test!")
            Joi.dit("Bravo tu as passer le test!")

Joi.reveille_toi()
