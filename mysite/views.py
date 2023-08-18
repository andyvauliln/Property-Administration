from django.shortcuts import render

def index(request):
    print("test1")
    all_dropdowns = [
        {
            'items': [
                {'id': 4, 'label': 'Default checkbox', 'checked': False},
                {'id': 5, 'label': 'Checked state', 'checked': True},
                {'id': 6, 'label': 'Default checkbox', 'checked': False}
            ],
            'onchange_function': "myFunction1(this.innerText)"
        },
         {
            'items': [
                {'id': 4, 'label': 'Default checkbox', 'checked': False},
                {'id': 5, 'label': 'Checked state', 'checked': True},
                {'id': 6, 'label': 'Default checkbox', 'checked': False}
            ],
            'onchange_function': "myFunction1(this.innerText)"
        },
    ]
    
    context = {
        'all_dropdowns': all_dropdowns
    }

    return render(request, 'index.html', context )


def dropdowns_view(request):
    context = {}
    print("test2")
    return render(request, 'components/dropdown.html', context)
